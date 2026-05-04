"""
Contrôleur d'une room : encapsule l'état d'UNE partie côté serveur.

Une instance par room active. Le ServerApp maintient un dict
{room_id: RoomController} et route les messages MQTT entrants vers
le bon contrôleur selon le room_id extrait du topic.

Cette classe est volontairement SANS MQTT et SANS threads :
  - Elle ne sait pas publier (le ServerApp s'en charge).
  - Elle ne sait pas armer de timer (le ServerApp s'en charge).
  - Elle est une "machine à états + données" pure et testable.

Toutes les méthodes qui modifient l'état sont nommées explicitement
(add_player, remove_player, set_ready, etc.) pour qu'on puisse tracer
les transitions à l'oral lors de la soutenance.

Étape 2 :
  - Constructeur (room_id, name).
  - to_index_entry() / to_state_payload() : sérialisation pour MQTT.
  - add_player / remove_player / is_empty : gestion de la présence.
  - seen_players : mémoire des pseudos ayant publié dans la room,
    utilisée par le ServerApp pour effacer les retained presence/<pseudo>
    au moment de la suppression de la room.

Étape 3 :
  - ready_players : ensemble des joueurs ayant cliqué "prêt".
  - set_ready(pseudo, ready) : met à jour le set ready.
  - can_start() : True si on peut démarrer (>= MIN_PLAYERS et tous prêts).
  - start_game() : transition LOBBY -> WRITE, fige players_order, calcule
    total_rounds et deadline_ts.

Étape 4 (cette version) :
  - submissions : dict[round_n][pseudo] -> payload, mémorise toutes les
    soumissions du round courant.
  - add_submission(round_n, pseudo, payload) : enregistre.
  - has_all_submissions(round_n) : True si tous les joueurs (online ET
    présents dans players_order) ont soumis pour ce round.
  - advance_round() : passe au round suivant ou termine la partie.
  - is_game_finished() : True après le dernier round.

Étapes suivantes (préparées dans la structure mais pas implémentées) :
  - 6 : phase REVEAL + retour au LOBBY.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from shared.protocol import (
    Phase,
    MIN_PLAYERS,
    DURATION_WRITE_S,
    phase_for_round,
    duration_for_phase,
)
from shared.rotation import total_rounds_for_players


@dataclass
class RoomController:
    """État d'une room.

    Mutable : toutes les mutations passent par des méthodes nommées.
    """

    # ---- identité (figés à la création) ----
    room_id: str
    name: str

    # ---- état de partie (étapes 3+) ----
    phase: Phase = Phase.LOBBY
    round_n: int = 0
    total_rounds: int = 0     # fixé au démarrage de la partie : == nb joueurs
    deadline_ts: int = 0      # 0 quand pas en phase chronométrée

    # players_order : ordre figé au démarrage de la partie. Vide en LOBBY.
    # Sert pour la rotation des albums (cf shared/rotation.py).
    players_order: list[str] = field(default_factory=list)

    # ---- données vivantes (pas dans le state payload mais utiles pour la logique) ----

    # Joueurs actuellement online dans cette room (alimenté par
    # les retained presence/<pseudo> reçus par le ServerApp).
    online_players: set[str] = field(default_factory=set)

    # Joueurs ayant cliqué "prêt" (étape 3).
    # Un joueur qui se déconnecte est automatiquement retiré de ce set
    # via remove_player() (un joueur offline ne peut pas être prêt).
    ready_players: set[str] = field(default_factory=set)

    # Soumissions reçues par round (étape 4).
    # Structure : {round_n: {pseudo: payload_dict}}.
    # On stocke le payload tel que reçu (sentence ou drawing) car on en
    # a besoin pour construire les albums lors de la consolidation.
    # Vidé au démarrage de chaque round dans advance_round().
    submissions: dict[int, dict[str, dict]] = field(default_factory=dict)

    # Mémoire de TOUS les pseudos qui ont publié au moins une fois
    # dans cette room (online ou offline). Sert au moment de la
    # suppression de la room : on doit effacer chaque retained
    # presence/<pseudo>, et il faut donc connaître la liste des pseudos
    # à effacer. On ne peut pas se contenter de online_players car un
    # joueur déjà parti (mais dont le retained offline est encore là)
    # doit aussi être nettoyé.
    seen_players: set[str] = field(default_factory=set)

    # ---- versioning ----

    # Compteur monotone incrémenté à chaque appel à to_state_payload().
    # Permet aux clients de détecter si un message est plus récent qu'un
    # autre (utile en cas de reconnexion : on garde le plus grand version).
    version: int = 0

    # ---------------------------------------------------------------
    # Sérialisation pour MQTT
    # ---------------------------------------------------------------

    def to_index_entry(self) -> dict:
        """Entrée pour le tableau 'rooms' de rooms-index.

        Format défini par shared/schemas.py:RoomIndexEntry.
        On y met les infos visibles depuis le menu principal :
        id, nom, nb joueurs en ligne, phase actuelle.
        """
        return {
            "id": self.room_id,
            "name": self.name,
            "n_players": len(self.online_players),
            "phase": self.phase.value,
        }

    def to_state_payload(self) -> dict:
        """Payload du retained rooms/<id>/state.

        Format défini par shared/schemas.py:StatePayload.
        Incrémente version à chaque appel : un appel = un publish.
        """
        self.version += 1
        return {
            "phase": self.phase.value,
            "round": self.round_n,
            "total_rounds": self.total_rounds,
            "deadline_ts": int(self.deadline_ts),
            "players_order": list(self.players_order),
            "room_id": self.room_id,
            "version": self.version,
        }

    # ---------------------------------------------------------------
    # Mutations présence (étape 2)
    # ---------------------------------------------------------------

    def add_player(self, pseudo: str) -> bool:
        """Ajoute un joueur en ligne. Retourne True si c'est une nouveauté.

        Le 'True si nouveau' permet au ServerApp de savoir s'il doit
        republier rooms-index (n_players a changé) ou pas (idempotence
        en cas de réception multiple du même retained).

        Met aussi à jour seen_players pour qu'on se souvienne de ce
        pseudo au moment de la suppression de la room.
        """
        self.seen_players.add(pseudo)
        if pseudo in self.online_players:
            return False
        self.online_players.add(pseudo)
        return True

    def remove_player(self, pseudo: str) -> bool:
        """Retire un joueur. Retourne True si effectivement retiré.

        Si le joueur avait cliqué prêt, on l'enlève aussi du set ready
        (un joueur offline ne peut pas être "prêt"). Important pour
        l'étape 3 : sinon can_start() pourrait être vrai alors qu'un
        des "prêts" n'est plus connecté.

        Note : on garde le pseudo dans seen_players, on ne l'oublie pas.
        Sinon on perdrait sa trace pour le clear final des retained.
        """
        # On marque ce pseudo comme vu (utile aussi quand un retained
        # offline arrive en premier, sans qu'on ait jamais vu d'online).
        self.seen_players.add(pseudo)
        if pseudo not in self.online_players:
            return False
        self.online_players.discard(pseudo)
        self.ready_players.discard(pseudo)
        return True

    def mark_seen(self, pseudo: str) -> None:
        """Marque un pseudo comme ayant publié dans cette room, sans
        l'ajouter aux online_players.

        Utile pour que le ServerApp puisse mémoriser un pseudo dont
        on reçoit un retained presence offline (donc qu'on n'ajoute pas
        aux online), de façon à pouvoir effacer son retained au moment
        de la suppression de la room.
        """
        self.seen_players.add(pseudo)

    def is_empty(self) -> bool:
        """True si plus aucun joueur en ligne. Sert à déclencher la
        suppression automatique de la room par le ServerApp.

        Note : on se base sur online_players, pas sur la présence des
        retained MQTT. Un joueur dont le retained presence n'a pas
        encore été publié à offline (mais qui est parti) sera retiré
        par le ServerApp via on_player_offline.
        """
        return len(self.online_players) == 0

    # ---------------------------------------------------------------
    # Système ready + démarrage (étape 3)
    # ---------------------------------------------------------------

    def set_ready(self, pseudo: str, ready: bool) -> bool:
        """Met à jour le statut "prêt" d'un joueur. Retourne True si
        l'état a réellement changé (utile pour décider s'il faut
        re-évaluer can_start()).

        Règle : seuls les joueurs online peuvent être prêts. Si un
        message ready arrive pour un joueur qu'on ne voit pas online
        (cas rare : retained ready arrivé avant retained presence),
        on l'ignore. Le ServerApp verra le presence ensuite et le
        client republie son ready au besoin.

        On n'autorise les changements de ready qu'en phase LOBBY :
        une fois la partie démarrée, le bouton "prêt" n'a plus de sens
        et un retained ready obsolète ne doit pas perturber la logique.
        """
        if self.phase != Phase.LOBBY:
            return False
        if pseudo not in self.online_players:
            return False

        was_ready = pseudo in self.ready_players
        if ready and not was_ready:
            self.ready_players.add(pseudo)
            return True
        if not ready and was_ready:
            self.ready_players.discard(pseudo)
            return True
        return False  # Pas de changement effectif

    def can_start(self) -> bool:
        """True si la partie peut démarrer automatiquement.

        Conditions :
          1. On est en phase LOBBY (sinon démarrage impossible/inutile).
          2. Au moins MIN_PLAYERS joueurs online.
          3. Tous les joueurs online sont prêts.

        La condition (3) est exprimée comme "online_players ⊆ ready_players".
        Comme remove_player() retire automatiquement du set ready, on a
        toujours ready_players ⊆ online_players, donc l'égalité des deux
        sets est équivalente à online_players ⊆ ready_players.
        """
        if self.phase != Phase.LOBBY:
            return False
        if len(self.online_players) < MIN_PLAYERS:
            return False
        return self.online_players == self.ready_players

    def start_game(self) -> None:
        """Transition LOBBY -> WRITE. Fige l'ordre des joueurs et calcule
        les paramètres de la partie.

        Précondition : can_start() doit être vrai (vérifié par le ServerApp
        avant l'appel). On lève une AssertionError sinon, pour détecter
        les bugs de logique côté ServerApp.

        Après cet appel :
          - phase = WRITE
          - round_n = 0
          - total_rounds = nb joueurs (cf shared/rotation.py)
          - deadline_ts = maintenant + DURATION_WRITE_S
          - players_order = liste triée alphabétiquement (déterministe)
          - ready_players est vidé (le bouton "prêt" n'a plus de sens
            une fois la partie lancée, et ça évite que d'anciens ready
            traînent quand on reviendra en LOBBY après un END)
        """
        assert self.can_start(), "start_game() appelé alors que can_start() est faux"

        # Tri alphabétique : déterministe et lisible à la démo.
        # Important : on fige l'ordre maintenant et on n'y touche plus
        # de toute la partie (la rotation des albums en dépend).
        self.players_order = sorted(self.online_players)
        self.total_rounds = total_rounds_for_players(len(self.players_order))
        self.round_n = 0
        self.phase = Phase.WRITE
        self.deadline_ts = int(time.time()) + DURATION_WRITE_S
        self.ready_players.clear()
        # Initialise le dict du round courant (vide pour l'instant).
        self.submissions = {0: {}}

    # ---------------------------------------------------------------
    # Système soumissions + progression de round (étape 4)
    # ---------------------------------------------------------------

    def add_submission(
        self,
        round_n: int,
        pseudo: str,
        payload: dict,
    ) -> bool:
        """Enregistre la soumission d'un joueur pour un round donné.

        Retourne True si c'est une nouvelle soumission (pas un doublon).
        Le ServerApp s'en sert pour décider s'il faut tester
        has_all_submissions() (early exit) — pas la peine de tester si
        rien n'a changé (même soumission renvoyée par exemple).

        Politique :
          - Si le round indiqué n'est pas le round courant, on ignore.
            Ça peut arriver avec un retained submission obsolète d'un
            round précédent qui arrive après une reconnexion. Le
            ServerApp aura nettoyé les retained par advance_round() →
            clear, mais on est défensif.
          - Si le pseudo n'est pas dans players_order, on ignore. Un
            joueur qui aurait rejoint après le démarrage ne participe
            pas (cas qui ne devrait pas arriver vu que la phase passe
            en WRITE → la GUI ne montre plus la room dans l'index, mais
            on est défensif).
          - Si le joueur a déjà soumis pour ce round, on écrase. Ça
            permet aux clients de se "rétracter" (rare, mais pas grave).
            Et ça évite qu'un retained republié par le client à la
            reconnexion crée un doublon.
        """
        if round_n != self.round_n:
            return False
        if pseudo not in self.players_order:
            return False

        # On garantit que le sous-dict existe (start_game et
        # advance_round l'ont normalement déjà créé).
        if round_n not in self.submissions:
            self.submissions[round_n] = {}

        was_new = pseudo not in self.submissions[round_n]
        self.submissions[round_n][pseudo] = payload
        return was_new

    def has_all_submissions(self, round_n: int) -> bool:
        """True si tous les joueurs de players_order ont soumis pour ce round.

        Note : on se base sur players_order (figé au démarrage), pas sur
        online_players. Si un joueur se déconnecte en plein round, on
        attend quand même sa soumission jusqu'à la deadline. À la
        deadline, le ServerApp comblera les manquants avec un
        placeholder serveur (cf FALLBACK_WORDS).
        """
        if round_n not in self.submissions:
            return False
        return len(self.submissions[round_n]) >= len(self.players_order)

    def missing_submitters(self, round_n: int) -> list[str]:
        """Liste des joueurs (de players_order) qui n'ont pas soumis ce round.

        Utilisée par le ServerApp à la deadline pour générer un
        placeholder pour chaque joueur manquant avant de consolider.
        """
        if round_n not in self.submissions:
            return list(self.players_order)
        submitted = set(self.submissions[round_n].keys())
        return [p for p in self.players_order if p not in submitted]

    def advance_round(self) -> None:
        """Passe au round suivant. Met à jour phase, round_n, deadline_ts.

        Précondition : on est en cours de partie (phase != LOBBY) et
        on n'est pas déjà au dernier round. Le ServerApp doit vérifier
        is_game_finished() AVANT d'appeler cette méthode.

        La phase suivante est calculée par phase_for_round() :
          - round 0   -> WRITE
          - round impair -> DRAW (dessine la phrase)
          - round pair (>=2) -> GUESS (devine le dessin)

        La deadline est recalculée à partir de duration_for_phase()
        pour la nouvelle phase. Ça permet d'avoir des durées différentes
        par phase sans complexifier ici (cf shared/protocol.py).

        Le dict des soumissions du round précédent est conservé tel quel :
        le ServerApp s'en est servi pour la consolidation des albums.
        On crée un sous-dict vide pour le nouveau round.
        """
        assert self.phase in (Phase.WRITE, Phase.DRAW, Phase.GUESS), \
            f"advance_round() en phase invalide : {self.phase}"
        assert not self.is_game_finished(), \
            "advance_round() appelé après le dernier round"

        self.round_n += 1
        self.phase = phase_for_round(self.round_n)
        self.deadline_ts = int(time.time()) + duration_for_phase(self.phase)
        self.submissions[self.round_n] = {}

    def is_game_finished(self) -> bool:
        """True si on vient de finir le dernier round.

        Le dernier round joué est total_rounds - 1 (rounds indexés 0..N-1).
        Donc si round_n == total_rounds - 1, on a fini la partie.

        Utilisé par le ServerApp APRÈS la consolidation du round courant
        pour décider : transition vers REVEAL (dernier round fini) ou
        advance_round() (round suivant à jouer).
        """
        if self.total_rounds == 0:
            # Sécurité : pas de partie en cours.
            return False
        return self.round_n >= self.total_rounds - 1

    def enter_reveal_phase(self) -> None:
        """Transition vers la phase REVEAL (étape 6, simplifié ici).

        À l'étape 4, on se contente de passer la phase à REVEAL et
        d'effacer la deadline (pas de timer en REVEAL pour l'instant).
        L'étape 6 implémentera le défilement des albums.

        Précondition : is_game_finished() doit être vrai.
        """
        assert self.is_game_finished(), \
            "enter_reveal_phase() appelée alors que la partie n'est pas finie"
        self.phase = Phase.REVEAL
        self.deadline_ts = 0