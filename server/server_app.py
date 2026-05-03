"""
Orchestrateur principal du serveur arbitre Gartic Phone.

Responsabilités à l'étape 2 :
  - Connexion au broker MQTT avec une LWT serveur globale.
  - Souscription aux wildcards globaux pour observer toutes les rooms.
  - Publication initiale de server/presence (online) et rooms-index.
  - Création de rooms à la réception de rooms-create.
  - Maintenance de rooms-index (republié à chaque mutation).
  - Gestion de la présence joueur : ajout/retrait dans la room.
  - Suppression automatique des rooms vides (avec clear des retained).
  - Reprise complète après crash : recréer les RoomController depuis
    les retained de l'index et des states, peupler online_players
    depuis les retained presence reçus pendant la fenêtre de reprise.
  - Arrêt propre : publication explicite de server/presence (offline)
    avant la déconnexion.

Étapes suivantes :
  - Étape 3 : démarrage automatique des parties (ready + transition LOBBY→WRITE).
  - Étape 4 : collecte des soumissions, consolidation des albums, boucle des rounds.
  - Étape 6 : phase REVEAL et END.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from shared import topics
from shared.mqtt_client import GarticMqttClient
from shared.protocol import Phase, PresenceStatus, MIN_PLAYERS

from server.room_controller import RoomController

log = logging.getLogger("server.app")

# Pseudo conventionnel pour le serveur dans les payloads de présence.
# Sert à identifier les messages "système" dans MQTT Explorer pendant la démo.
SERVER_PSEUDO = "__server__"

# Délai de la fenêtre de reprise au démarrage. Pendant cette fenêtre, on
# accumule les retained reçus avant de publier notre propre état.
# 1.5s est une marge confortable sur le broker public emqx (latence ~100ms).
RECOVERY_WINDOW_S = 1.5


class ServerApp:
    """Orchestrateur multi-rooms du serveur Gartic.

    Ne gère pas encore de room concrète à l'étape 1 — sa seule mission est
    de poser les fondations : connexion broker, LWT, souscriptions globales,
    publication des topics globaux (server/presence, rooms-index).
    """

    def __init__(self) -> None:
        # Dictionnaire room_id -> contrôleur de room.
        # Une instance par room active. À l'étape 2A on le remplit via
        # _on_rooms_create. À l'étape 2B la reprise après crash le pré-remplira
        # à partir des retained.
        self.rooms: Dict[str, RoomController] = {}

        # Lock pour protéger self.rooms et self._index_version, accédés
        # potentiellement depuis plusieurs callbacks paho (qui tournent
        # dans le thread paho) et le main thread.
        self._rooms_lock = threading.Lock()

        # Compteur monotone publié dans rooms-index. Incrémenté à chaque
        # republication. Permet aux clients de détecter un index plus
        # récent qu'un autre.
        self._index_version: int = 0

        # Évènement signalé une fois la fenêtre de reprise écoulée.
        # Tant qu'il n'est pas set, on accumule les retained reçus
        # plutôt que de publier notre propre état.
        self._recovery_done = threading.Event()

        # Évènement signalé par stop() pour faire sortir run() de sa boucle.
        self._shutdown = threading.Event()

        # Timers de fin de round, indexés par room_id (étape 3).
        # Un seul timer actif à la fois par room. Stocké pour pouvoir
        # l'annuler proprement si la room est supprimée pendant la partie
        # ou si le serveur s'arrête.
        # À l'étape 3 le callback du timer ne fait rien (no-op). À l'étape
        # 4 il déclenchera la consolidation des soumissions.
        self._round_timers: Dict[str, threading.Timer] = {}

        # Configuration du client MQTT avec la LWT serveur globale.
        # Le payload de la LWT est ce que le broker publiera automatiquement
        # si on disparaît sans préavis (kill -9, perte réseau prolongée).
        # Note : ts=0 dans la LWT car on ne peut pas mettre un timestamp
        # significatif au moment de la connexion (il serait dépassé). C'est
        # voulu : les clients ne se servent pas de ts pour ce topic.
        will_payload = {
            "status": PresenceStatus.OFFLINE.value,
            "pseudo": SERVER_PSEUDO,
            "ts": 0,
        }
        self.mqtt = GarticMqttClient(
            client_id="gartic-server",
            will_topic=topics.t_server_presence(),
            will_payload=will_payload,
            will_qos=1,
            will_retain=True,
        )

        # Enregistrement des handlers AVANT connexion : comme ça les
        # subscriptions seront automatiquement faites par le wrapper
        # dans son on_connect.
        self._register_handlers()

        # on_ready() est appelé à chaque (re)connexion réussie. C'est là
        # qu'on déclenche la fenêtre de reprise (au premier appel) ou
        # qu'on republie la présence (en cas de reconnexion).
        self.mqtt.on_ready(self._on_mqtt_ready)

    # ------------------------------------------------------------------
    # Souscriptions
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Enregistre tous les handlers MQTT.

        On souscrit aux wildcards globaux, ce qui nous permet d'observer
        tous les évènements de toutes les rooms en une seule souscription.
        Le routage par room_id se fait au moment du callback, en extrayant
        le room_id depuis le topic reçu.
        """
        # rooms-index retained : sert à la reprise au démarrage
        # (on récupère la liste des rooms qui existaient avant un crash).
        self.mqtt.on_message_for(
            topics.t_rooms_index(),
            self._on_rooms_index_retained,
            qos=1,
        )

        # rooms-create : déclenché par les clients pour créer une room.
        # QoS 0 par contrat (best-effort, le client a un timeout en GUI).
        self.mqtt.on_message_for(
            topics.t_rooms_create(),
            self._on_rooms_create,
            qos=0,
        )

        # rooms/+/state retained : pour la reprise après crash. Hors
        # reprise, ces messages sont nos propres échos qu'on ignore.
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/state",
            self._on_room_state_retained,
            qos=1,
        )

        # Wildcards globaux pour observer tous les évènements joueurs
        # de toutes les rooms. On extrait le room_id du topic au moment
        # du routage.
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/presence/+",
            self._on_player_presence,
            qos=1,
        )
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/ready/+",
            self._on_player_ready,
            qos=1,
        )
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/submissions/round/+/+",
            self._on_player_submission,
            qos=1,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Lance le serveur et bloque jusqu'à stop()."""
        log.info("Démarrage du serveur Gartic")
        self.mqtt.connect_and_loop()

        if not self.mqtt.wait_until_connected(timeout_s=10.0):
            log.error("Impossible de se connecter au broker en 10s")
            return

        log.info("Serveur en attente d'évènements (Ctrl+C pour arrêter)")
        # Boucle principale : on dort, le vrai travail se passe dans les
        # callbacks MQTT (thread paho) et les timers (à l'étape 3+).
        #
        # IMPORTANT : on ne fait PAS self._shutdown.wait() sans timeout.
        # Sur Linux, threading.Event.wait() bloque dans une fonction C qui
        # empêche Python de traiter les signaux (SIGINT/Ctrl+C). On boucle
        # donc avec un petit timeout, ce qui laisse le signal handler
        # s'exécuter entre les itérations.
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=0.5)
        log.info("Boucle principale terminée")

    def stop(self) -> None:
        """Arrêt propre : publication offline puis déconnexion.

        Important : on publie EXPLICITEMENT server/presence à offline avant
        de déconnecter. La LWT n'est PAS déclenchée lors d'une déconnexion
        propre (c'est tout l'intérêt de la LWT : elle ne se déclenche que
        sur disparition non annoncée). Sans cette publication explicite,
        les clients verraient le serveur encore "online" pendant que paho
        ferme la connexion.
        """
        log.info("Arrêt demandé, publication offline")

        # Annule les timers de round en cours pour éviter qu'ils se
        # déclenchent pendant l'arrêt et tentent de publier sur une
        # connexion fermée. cancel() est idempotent et thread-safe.
        for room_id, timer in list(self._round_timers.items()):
            timer.cancel()
        self._round_timers.clear()

        try:
            self.mqtt.publish_json(
                topics.t_server_presence(),
                {
                    "status": PresenceStatus.OFFLINE.value,
                    "pseudo": SERVER_PSEUDO,
                    "ts": int(time.time()),
                },
                qos=1,
                retain=True,
            )
            # Petite pause pour laisser paho envoyer le PUBLISH avant
            # la déconnexion. Sans ça, le QoS 1 peut être interrompu.
            time.sleep(0.3)
        except Exception:
            log.exception("Erreur lors du publish offline (on continue)")

        self.mqtt.disconnect()
        self._shutdown.set()
        log.info("Serveur arrêté")

    # ------------------------------------------------------------------
    # Callbacks MQTT
    # ------------------------------------------------------------------

    def _on_mqtt_ready(self) -> None:
        """Appelé à chaque (re)connexion MQTT réussie.

        Au PREMIER appel (au boot), on lance la fenêtre de reprise puis,
        à la fin de cette fenêtre, on publie notre présence et l'index.

        Aux appels suivants (reconnexions après coupure), on republie
        directement notre présence — la reprise n'a pas à être refaite
        car le serveur est resté actif en mémoire.
        """
        if self._recovery_done.is_set():
            # Cas reconnexion : on republie juste notre présence.
            log.info("Reconnexion détectée, republication de server/presence")
            self._publish_server_online()
            return

        # Cas premier boot : on déclenche la fenêtre de reprise dans un
        # thread dédié pour ne pas bloquer le callback paho.
        log.info(
            "Connexion initiale, fenêtre de reprise (%.1fs) avant publish",
            RECOVERY_WINDOW_S,
        )
        threading.Thread(
            target=self._run_recovery_window,
            name="recovery-window",
            daemon=True,
        ).start()

    def _run_recovery_window(self) -> None:
        """Attend la fin de la fenêtre de reprise puis publie l'état serveur.

        Pendant la fenêtre, les retained reçus sont accumulés par les
        handlers (_on_rooms_index_retained, _on_room_state_retained,
        _on_player_presence) qui mettent à jour self.rooms mais NE
        publient PAS (le flag self._recovery_done n'est pas encore set).

        Une fois la fenêtre écoulée, on :
          1. Set le flag _recovery_done : à partir de maintenant les
             handlers publient à nouveau (les retained suivants seront
             traités comme du live).
          2. Publie server/presence online.
          3. Republie les states retained reconstitués (au cas où
             notre state interne aurait des infos plus à jour que les
             retained existants — par exemple si on a reçu des presence
             pendant la fenêtre, n_players a bougé).
          4. Republie rooms-index reconstitué.

        L'ordre est important : on publie SOI-même online APRÈS avoir
        publié les states. Comme ça, dès qu'un client voit le serveur
        online, il sait que les autres retained sont déjà à jour.
        """
        time.sleep(RECOVERY_WINDOW_S)
        self._recovery_done.set()
        log.info("Fenêtre de reprise terminée")

        # Si on a reconstitué des rooms, on republie leurs states pour
        # que les clients aient un index et des states cohérents.
        with self._rooms_lock:
            recovered_rooms = list(self.rooms.values())

        if recovered_rooms:
            log.info(
                "Reprise effective : %d rooms reconstituées, republication",
                len(recovered_rooms),
            )
            for room in recovered_rooms:
                self._publish_room_state(room)

        # Republie l'index AVEC les rooms reconstituées (n_players et
        # phase à jour).
        self._publish_rooms_index()

        # Annonce de notre présence en dernier : le client comprend
        # qu'à partir de ce moment le serveur est opérationnel.
        self._publish_server_online()

    def _publish_server_online(self) -> None:
        """Publie server/presence à online (retained)."""
        self.mqtt.publish_json(
            topics.t_server_presence(),
            {
                "status": PresenceStatus.ONLINE.value,
                "pseudo": SERVER_PSEUDO,
                "ts": int(time.time()),
            },
            qos=1,
            retain=True,
        )
        log.info("server/presence publié (online)")

    def _publish_rooms_index(self) -> None:
        """Publie le rooms-index retained à partir de l'état interne.

        Construit le payload depuis self.rooms en demandant à chaque
        RoomController son to_index_entry().

        À appeler après chaque mutation de self.rooms (création, suppression)
        OU après tout changement qui affecte une entrée d'index (n_players
        qui change, phase qui change). Concrètement appelée par :
          - _on_rooms_create (étape 2A)
          - _on_player_presence (étape 2B)
          - les transitions de phase dans RoomController (étapes 3+)
        """
        with self._rooms_lock:
            self._index_version += 1
            payload = {
                "rooms": [
                    room.to_index_entry()
                    for room in self.rooms.values()
                ],
                "version": self._index_version,
            }
        self.mqtt.publish_json(
            topics.t_rooms_index(),
            payload,
            qos=1,
            retain=True,
        )
        log.info(
            "rooms-index publié (version=%d, %d rooms)",
            payload["version"], len(payload["rooms"]),
        )

    def _publish_room_state(self, room: RoomController) -> None:
        """Publie le retained rooms/<id>/state pour une room donnée.

        Appelé :
          - À la création de la room (état initial LOBBY).
          - Aux transitions de phase (étapes 3+).
          - À la republication du state lors d'un changement de joueur en
            LOBBY (la liste des joueurs étant utilisée par les clients
            pour afficher le lobby).

        Note : to_state_payload() incrémente self.version dans la room.
        Donc chaque appel à cette méthode produit un payload avec une
        version strictement croissante, ce qui permet aux clients de
        détecter les messages dans le désordre.
        """
        payload = room.to_state_payload()
        self.mqtt.publish_json(
            topics.t_state(room.room_id),
            payload,
            qos=1,
            retain=True,
        )
        log.info(
            "[%s] state publié : phase=%s round=%d/%d version=%d",
            room.room_id,
            payload["phase"],
            payload["round"],
            payload["total_rounds"],
            payload["version"],
        )

    def _delete_room(self, room_id: str) -> None:
        """Supprime une room et nettoie tous ses retained.

        Appelée quand le dernier joueur quitte une room (online_players
        devient vide). Ce nettoyage est crucial : sans lui, des retained
        orphelins traîneraient sur le broker et pollueraient les démarrages
        suivants (la reprise les retrouverait et recréerait des rooms
        fantômes vides).

        Liste des retained à effacer pour une room :
          - rooms/<id>/state
          - rooms/<id>/presence/<pseudo>  (un par pseudo qu'on a vu)
          - rooms/<id>/ready/<pseudo>     (idem)
          - rooms/<id>/reveal/current     (étape 6)
          - rooms/<id>/albums/...         (étape 4, pas encore présents en
                                            étape 2)
          - rooms/<id>/submissions/...    (étape 4, pas encore présents)

        À l'étape 2 on n'a que les 4 premiers types à gérer. À l'étape
        4+ on ajoutera albums et submissions.

        On retire la room du dico AVANT le clear pour éviter qu'un
        retained presence offline qui arriverait pendant le clear ne
        re-trigger _delete_room.
        """
        with self._rooms_lock:
            room = self.rooms.pop(room_id, None)
        if room is None:
            log.debug("Tentative de suppression d'une room inconnue : %s", room_id)
            return

        # Annule le timer de round si la room était en cours de partie.
        # Sans ça, le callback s'exécuterait sur une room déjà supprimée
        # (le callback gère ce cas mais autant être propre).
        timer = self._round_timers.pop(room_id, None)
        if timer is not None:
            timer.cancel()
            log.info("[%s] Timer de round annulé (suppression room)", room_id)

        log.info(
            "[%s] Suppression de la room (joueurs vus : %d)",
            room_id, len(room.seen_players),
        )

        # Clear du state retained.
        self.mqtt.clear_retained(topics.t_state(room_id))

        # Clear de chaque retained presence/<pseudo> et ready/<pseudo>
        # pour les pseudos qu'on a vus passer.
        for pseudo in room.seen_players:
            self.mqtt.clear_retained(topics.t_player_presence(room_id, pseudo))
            self.mqtt.clear_retained(topics.t_player_ready(room_id, pseudo))

        # Clear de reveal/current (peut ne pas exister mais clear est
        # idempotent : si rien n'était retained, ça ne fait rien).
        self.mqtt.clear_retained(topics.t_reveal_current(room_id))

        # Republie l'index sans cette room.
        self._publish_rooms_index()

    # ------------------------------------------------------------------
    # Handlers de messages
    # ------------------------------------------------------------------

    def _on_rooms_index_retained(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Reçu pendant la fenêtre de reprise OU après nos propres publishes.

        Comportement :
          - Si on est APRÈS la fenêtre de reprise → c'est l'écho de notre
            propre publish, on ignore.
          - Si on est PENDANT la fenêtre de reprise et que l'index contient
            des rooms → on recrée des RoomController (squelettes vides) à
            partir des entrées d'index. Les retained `state` et `presence`
            qu'on est en train de recevoir en parallèle vont compléter ces
            squelettes (dans _on_room_state_retained et _on_player_presence).

        Une fois la fenêtre de reprise terminée, _run_recovery_window()
        republie l'index reconstitué (avec les bons n_players à jour
        grâce aux presence retained reçus entre temps).
        """
        if self._recovery_done.is_set():
            # C'est l'écho de notre propre publish, rien à faire.
            return

        if not isinstance(payload, dict):
            return

        existing = payload.get("rooms", [])
        if not existing:
            log.info("Aucune room existante (index vide ou absent)")
            return

        log.info(
            "Reprise : %d rooms détectées dans l'index (%s)",
            len(existing), [r.get("id") for r in existing],
        )

        # Recrée des RoomController squelettes pour chaque room de l'index.
        # Les détails (phase, round, players_order) seront remplis par
        # les retained `state` qui vont arriver, et les online_players
        # par les retained `presence`.
        with self._rooms_lock:
            for entry in existing:
                room_id = entry.get("id")
                name = entry.get("name", "?")
                if not isinstance(room_id, str) or room_id in self.rooms:
                    continue
                room = RoomController(room_id=room_id, name=name)
                # On essaie de remettre la phase si l'index la donne, mais
                # ce n'est qu'un fallback : le retained `state` la remplacera.
                try:
                    room.phase = Phase(entry.get("phase", "LOBBY"))
                except ValueError:
                    room.phase = Phase.LOBBY
                self.rooms[room_id] = room
                log.info("[%s] Room reconstruite (squelette) : name=%r", room_id, name)

    def _on_room_state_retained(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Reçu pour `rooms/<id>/state`.

        Pendant la fenêtre de reprise, ce handler complète le squelette
        de RoomController créé par _on_rooms_index_retained avec les
        détails de la partie en cours (phase, round, deadline, etc.).

        Hors fenêtre de reprise, c'est l'écho de notre propre publish.
        On ignore alors.
        """
        if self._recovery_done.is_set():
            return
        if not isinstance(payload, dict):
            return

        # Format topic : <PREFIX>/rooms/<room_id>/state
        parts = topic.split("/")
        try:
            room_id = parts[-2]
        except IndexError:
            return

        with self._rooms_lock:
            room = self.rooms.get(room_id)

        if room is None:
            log.debug(
                "state retained reçu pour room inconnue %s pendant reprise",
                room_id,
            )
            return

        # Reconstruit l'état à partir du payload retained.
        try:
            room.phase = Phase(payload.get("phase", "LOBBY"))
            room.round_n = int(payload.get("round", 0))
            room.total_rounds = int(payload.get("total_rounds", 0))
            room.deadline_ts = int(payload.get("deadline_ts", 0))
            room.players_order = list(payload.get("players_order", []))
            # On reprend la version pour rester monotone : la prochaine
            # publication aura version+1, ce qui évite que les clients
            # voient un retour en arrière.
            room.version = int(payload.get("version", 0))
        except (ValueError, TypeError):
            log.exception("[%s] state retained mal formé, room en LOBBY", room_id)
            return

        log.info(
            "[%s] state restauré : phase=%s round=%d/%d version=%d",
            room_id, room.phase.value, room.round_n,
            room.total_rounds, room.version,
        )

    def _on_rooms_create(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Demande de création de room par un client.

        Format payload attendu (cf shared/schemas.py:RoomsCreatePayload) :
            {"room_id": "<hex 6>", "name": "<str>"}

        Politique :
          - Validation minimale : room_id et name doivent être présents et
            non vides.
          - Si l'id existe déjà → on log un warning et on ignore. Le client
            verra son timeout côté GUI et pourra retenter avec un autre id.
            (Décision actée : pas de feedback explicite côté client.)
          - Sinon : on crée le RoomController, on l'ajoute au dico, on
            publie le state retained initial, on republie l'index.

        Note : ce handler tourne dans le thread paho (callback de
        message_callback_add). Toutes les manipulations de self.rooms
        sont protégées par self._rooms_lock.
        """
        # Validation du format.
        if not isinstance(payload, dict):
            log.warning("rooms-create reçu avec payload non-dict : %r", payload)
            return
        room_id = payload.get("room_id")
        name = payload.get("name")
        if not isinstance(room_id, str) or not room_id:
            log.warning("rooms-create avec room_id invalide : %r", payload)
            return
        if not isinstance(name, str) or not name:
            log.warning("rooms-create avec name invalide : %r", payload)
            return

        # Création + check de collision sous lock.
        # On ne libère le lock qu'après avoir ajouté la room, ce qui
        # garantit qu'aucune autre création concurrente avec le même id
        # ne passera (improbable mais possible si deux clients génèrent
        # le même hex en même temps).
        with self._rooms_lock:
            if room_id in self.rooms:
                # Décision actée : on ignore en silence côté MQTT.
                # Le warning est utile pour le debug pendant la démo.
                log.warning(
                    "Collision room_id=%s (existant: name=%r, demandé: name=%r) — ignoré",
                    room_id,
                    self.rooms[room_id].name,
                    name,
                )
                return
            room = RoomController(room_id=room_id, name=name)
            self.rooms[room_id] = room

        log.info("Room créée : id=%s name=%r", room_id, name)

        # Publication du state retained initial de la room.
        # Important : c'est ce que le client surveille (en plus de l'index)
        # pour savoir qu'il peut entrer dans la room.
        self._publish_room_state(room)

        # Republication de l'index pour que tous les clients voient
        # apparaître la nouvelle room dans leur menu.
        self._publish_rooms_index()

    def _on_player_presence(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Présence d'un joueur dans une room.

        Ce handler reçoit deux types de messages :
          - Les publishes "live" des clients (un joueur passe online ou
            offline).
          - Les retained reçus pendant la fenêtre de reprise après un
            redémarrage du serveur (utilisés pour reconstruire l'état).

        Politique :
          - Si la room n'existe pas dans self.rooms, on ignore. Cas
            possible : un retained presence d'une room déjà supprimée
            qu'on n'a pas encore réussi à effacer.
          - Si online → add_player. Si nouveauté → republie l'index.
          - Si offline → remove_player. Si la room devient vide → on la
            supprime (delete + clear retained).

        Pendant la fenêtre de reprise, ce handler est aussi déclenché
        par les retained mais le comportement est exactement le même
        (la reprise consiste justement à reconstruire l'état à partir
        de ces retained).
        """
        # Format du topic : <PREFIX>/rooms/<room_id>/presence/<pseudo>
        room_id, pseudo = self._extract_room_and_user(topic)
        if room_id is None:
            return

        # Payload vide (None) : c'est un "clear retained" — soit le nôtre
        # qu'on entend en écho après un _delete_room, soit celui d'un
        # client qui s'est désinscrit proprement. Aucune action utile.
        if payload is None:
            log.debug("presence vide (clear retained) sur %s — ignoré", topic)
            return

        if not isinstance(payload, dict):
            log.warning("presence avec payload inattendu : topic=%s payload=%r",
                        topic, payload)
            return

        status = payload.get("status")
        if status not in (PresenceStatus.ONLINE.value, PresenceStatus.OFFLINE.value):
            log.warning("presence avec status inconnu (%r) : topic=%s", status, topic)
            return

        # Recherche de la room cible.
        with self._rooms_lock:
            room = self.rooms.get(room_id)

        if room is None:
            # Room inconnue : ça peut arriver si on reçoit un retained
            # d'une room déjà supprimée (le retained survit jusqu'à ce
            # qu'on le clear). On efface ce retained orphelin pour
            # nettoyer le broker.
            log.info(
                "presence reçu pour room inconnue %s, effacement du retained",
                room_id,
            )
            self.mqtt.clear_retained(topic)
            return

        # Application au RoomController.
        if status == PresenceStatus.ONLINE.value:
            is_new = room.add_player(pseudo)
            if is_new:
                log.info(
                    "[%s] %s rejoint la room (online=%d)",
                    room_id, pseudo, len(room.online_players),
                )
                # On republie l'index parce que n_players a changé.
                self._publish_rooms_index()
        else:
            # offline
            was_present = room.remove_player(pseudo)
            # On marque seen même si le joueur n'était pas dans online :
            # ça peut être un retained offline orphelin qui arrive en
            # reprise, on veut quand même pouvoir nettoyer son retained
            # à la fin.
            room.mark_seen(pseudo)

            if was_present:
                log.info(
                    "[%s] %s quitte la room (online=%d)",
                    room_id, pseudo, len(room.online_players),
                )
                # Étape 3 : on efface aussi le retained ready/<pseudo>.
                # remove_player() a retiré le pseudo du set ready côté
                # serveur, mais le retained MQTT subsiste. Sans ce clear,
                # un joueur qui se reconnecterait verrait son ancien
                # ready=true via le retained et apparaîtrait prêt sans
                # avoir cliqué. Le clear est idempotent.
                self.mqtt.clear_retained(topics.t_player_ready(room_id, pseudo))

            # Si la room est vide, on la supprime.
            # On vérifie cette condition même si was_present est False :
            # imagine un retained offline arrivant pour la dernière
            # personne au démarrage du serveur ; on doit quand même
            # supprimer la room.
            if room.is_empty():
                self._delete_room(room_id)
            elif was_present:
                # Pas vide mais quelqu'un est parti : republie l'index.
                self._publish_rooms_index()

    def _on_player_ready(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """État ready d'un joueur (étape 3).

        Met à jour le set ready_players de la room, puis évalue si la
        partie peut démarrer automatiquement (cf RoomController.can_start).
        Si oui, déclenche start_game() et publie les nouveaux états.

        Cas particuliers gérés :
          - payload vide (clear retained) : ignoré, c'est un nettoyage.
          - room inconnue : retained orphelin, on l'efface du broker.
          - joueur pas encore vu online : set_ready retournera False et
            on n'évalue pas can_start. Le client republiera son ready
            à la prochaine reconnexion (cf fake_client.on_room_ready).
          - phase != LOBBY : set_ready retourne False, le retained
            obsolète n'a aucun effet. Utile en cas de reprise après
            crash en plein WRITE.
        """
        room_id, pseudo = self._extract_room_and_user(topic)
        if room_id is None:
            return

        # Payload vide = clear retained. On ignore silencieusement
        # (publié par _delete_room ou par _clear_room_ready).
        if payload is None:
            log.debug("ready vide (clear retained) sur %s — ignoré", topic)
            return

        if not isinstance(payload, dict):
            log.warning("ready avec payload inattendu : topic=%s payload=%r",
                        topic, payload)
            return

        ready_value = payload.get("ready")
        if not isinstance(ready_value, bool):
            log.warning("ready avec valeur invalide (%r) : topic=%s",
                        ready_value, topic)
            return

        # Recherche de la room cible.
        with self._rooms_lock:
            room = self.rooms.get(room_id)

        if room is None:
            # Retained orphelin d'une room déjà supprimée. On nettoie.
            log.info(
                "ready reçu pour room inconnue %s, effacement du retained",
                room_id,
            )
            self.mqtt.clear_retained(topic)
            return

        # Mise à jour du set ready dans la room. set_ready() retourne
        # True uniquement s'il y a eu un vrai changement d'état (et que
        # le joueur est online + on est en LOBBY).
        changed = room.set_ready(pseudo, ready_value)
        if not changed:
            # Pas de changement effectif : soit le joueur n'est pas online,
            # soit on n'est plus en LOBBY, soit le ready était déjà à cette
            # valeur. Rien à propager.
            log.debug(
                "[%s] ready/%s = %s sans effet (online=%s, phase=%s)",
                room_id, pseudo, ready_value,
                pseudo in room.online_players,
                room.phase.value,
            )
            return

        log.info(
            "[%s] %s passe à ready=%s (%d/%d prêts)",
            room_id, pseudo, ready_value,
            len(room.ready_players), len(room.online_players),
        )

        # Vérifie si on peut démarrer la partie automatiquement.
        self._check_and_maybe_start(room)

    def _check_and_maybe_start(self, room: RoomController) -> None:
        """Vérifie can_start() et démarre la partie si la condition est vraie.

        Extraite en méthode séparée pour deux raisons :
          1. Lisibilité : _on_player_ready reste focalisé sur le parsing
             et la mise à jour, le démarrage est une responsabilité distincte.
          2. Réutilisabilité : à l'étape 6 (retour LOBBY après END), on
             pourra rappeler can_start sans passer par un message ready.

        Étapes du démarrage (séquence importante pour la cohérence) :
          1. RoomController.start_game() : transition LOBBY → WRITE,
             fige players_order, calcule deadline_ts.
          2. Publie le state retained avec phase=WRITE. Les clients
             utilisent ce message pour basculer vers leur WriteScreen.
          3. Republie rooms-index : la phase de la room a changé,
             les autres clients (dans le menu) doivent voir l'update.
          4. Arme le timer du round 0 sur DURATION_WRITE_S. À l'étape 3,
             le callback ne fait rien ; à l'étape 4 il déclenchera la
             consolidation des soumissions.
        """
        if not room.can_start():
            return

        log.info(
            "[%s] Démarrage automatique : %d joueurs tous prêts",
            room.room_id, len(room.online_players),
        )
        room.start_game()

        # Ordre : state d'abord (les clients dans la room basculent),
        # index ensuite (les clients hors room voient le changement).
        self._publish_room_state(room)
        self._publish_rooms_index()

        # Calcule la durée restante du round à partir de la deadline
        # plutôt qu'en hardcodant DURATION_WRITE_S : c'est plus robuste
        # (si on ajoute une logique d'ajustement de deadline plus tard,
        # le timer suivra automatiquement).
        remaining_s = max(0.0, room.deadline_ts - time.time())
        self._arm_round_timer(room.room_id, room.round_n, remaining_s)

    def _arm_round_timer(
        self,
        room_id: str,
        round_n: int,
        duration_s: float,
    ) -> None:
        """Arme un threading.Timer pour la fin du round courant.

        À l'étape 3, le callback est un no-op : on log juste que le timer
        a expiré. À l'étape 4, il déclenchera la consolidation des
        soumissions (collecte + rotation + publication des albums) puis
        la transition vers le round suivant.

        Si un timer était déjà actif pour cette room, on l'annule avant
        d'en armer un nouveau (ne devrait pas arriver en flux normal,
        mais c'est une sécurité contre les bugs futurs).

        Le Timer tourne dans son propre thread démon. cancel() est
        thread-safe : si l'expiration est déjà en cours, cancel() n'a
        pas d'effet ; le callback doit donc vérifier que l'état est
        cohérent avant d'agir (par ex. la room existe-t-elle encore).
        """
        # Annule un éventuel timer précédent pour cette room.
        previous = self._round_timers.pop(room_id, None)
        if previous is not None:
            previous.cancel()

        timer = threading.Timer(
            duration_s,
            self._on_round_timer_expired,
            args=(room_id, round_n),
        )
        timer.name = f"round-timer-{room_id}-r{round_n}"
        timer.daemon = True
        self._round_timers[room_id] = timer
        timer.start()
        log.info(
            "[%s] Timer round %d armé pour %.1fs",
            room_id, round_n, duration_s,
        )

    def _on_round_timer_expired(self, room_id: str, round_n: int) -> None:
        """Callback exécuté à l'expiration du timer de round.

        À l'étape 3 : NO-OP. On log juste pour confirmer que le câblage
        marche. À l'étape 4 ce callback déclenchera la consolidation
        (collecte des soumissions + rotation + publication albums +
        transition vers round suivant ou phase REVEAL).

        Avant d'agir, on vérifie que la room existe encore et que le
        round indiqué correspond bien au round courant : un timer en
        retard sur une room déjà passée à un round suivant ne doit pas
        re-déclencher de consolidation. (Ne devrait pas arriver en flux
        normal grâce à _arm_round_timer.cancel(), mais robustesse.)
        """
        # On retire le timer du dico : il a fait son office.
        self._round_timers.pop(room_id, None)

        with self._rooms_lock:
            room = self.rooms.get(room_id)

        if room is None:
            log.debug(
                "Timer round %d expiré pour room %s déjà supprimée — ignoré",
                round_n, room_id,
            )
            return

        if room.round_n != round_n:
            log.debug(
                "[%s] Timer round %d expiré mais round courant = %d — ignoré",
                room_id, round_n, room.round_n,
            )
            return

        # ÉTAPE 3 : on s'arrête ici. La logique réelle viendra en étape 4.
        log.info(
            "[%s] Timer round %d expiré (no-op étape 3, sera branché en étape 4)",
            room_id, round_n,
        )

    def _on_player_submission(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Soumission d'un joueur (étape 4)."""
        # Format : <PREFIX>/rooms/<room_id>/submissions/round/<n>/<pseudo>
        parts = topic.split("/")
        try:
            room_id = parts[-5]
            round_n = int(parts[-2])
            pseudo = parts[-1]
        except (IndexError, ValueError):
            log.warning("Topic submission mal formé : %s", topic)
            return
        sub_type = payload.get("type") if isinstance(payload, dict) else None
        log.info(
            "[%s] submission round=%d %s (par %s, retain=%s)",
            room_id, round_n, sub_type, pseudo, retain,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_room_and_user(topic: str) -> tuple[str | None, str | None]:
        """Extrait (room_id, pseudo) d'un topic de la forme
        <PREFIX>/rooms/<room_id>/<feature>/<pseudo>.
        Retourne (None, None) si le format est inattendu.
        """
        parts = topic.split("/")
        # Le topic complet a au moins 6 segments : prefix0/prefix1/rooms/<id>/<feat>/<pseudo>
        # avec PREFIX = "isen-2026-VTGC/gartic" qui occupe 2 segments.
        if len(parts) < 6 or parts[-4] != "rooms":
            return None, None
        return parts[-3], parts[-1]