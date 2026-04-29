"""
Faux serveur — utilisé par Dev B pour tester le client GUI sans le vrai serveur.

Adapté à la nouvelle architecture (mai 2025) :
  - Serveur unique multi-rooms.
  - Index global rooms-index avec liste JSON.
  - LWT serveur globale.
  - Création de rooms par messages rooms-create.

Différences avec le vrai serveur :
  - Phases TRÈS courtes (5s par défaut) pour tester rapidement les
    transitions de la GUI sans attendre 90s par round.
  - Rotation simplifiée : on republie les soumissions reçues telles
    quelles dans les albums (pas de placeholder, pas de gestion des
    joueurs absents).
  - Pas de gestion fine de la fin de partie ni du retour au lobby.

Usage :
    python -m tests.manual.fake_server

    # Avec des phases plus longues pour avoir le temps de tester manuellement :
    python -m tests.manual.fake_server --phase-duration 30
"""

import argparse
import logging
import threading
import time
from typing import Dict, List, Optional, Set

from shared import topics
from shared.mqtt_client import GarticMqttClient
from shared.protocol import (
    Phase, PresenceStatus, SubmissionType,
    MIN_PLAYERS, phase_for_round,
)
from shared.rotation import (
    album_id_for_index,
    album_assigned_to_player,
    total_rounds_for_players,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fake_server")


SERVER_PSEUDO = "__server__"


class FakeRoom:
    """État d'une room côté fake-server. Volontairement minimal."""

    def __init__(self, room_id: str, name: str):
        self.room_id = room_id
        self.name = name
        self.phase: str = Phase.LOBBY.value
        self.round: int = 0
        self.total_rounds: int = 0
        self.players_order: List[str] = []
        self.deadline_ts: int = 0
        self.version: int = 0

        # Présence et ready en temps réel.
        self.online_players: Set[str] = set()
        self.ready_players: Set[str] = set()

        # Soumissions reçues : {round: {pseudo: payload}}
        self.submissions: Dict[int, Dict[str, dict]] = {}

        # Timer en cours (pour les transitions de phase).
        self.phase_timer: Optional[threading.Timer] = None

        # Lock pour les accès concurrents au state de cette room.
        self.lock = threading.Lock()

    def to_index_entry(self) -> dict:
        return {
            "id": self.room_id,
            "name": self.name,
            "n_players": len(self.online_players),
            "phase": self.phase,
        }

    def to_state_payload(self) -> dict:
        self.version += 1
        return {
            "phase": self.phase,
            "round": self.round,
            "total_rounds": self.total_rounds,
            "deadline_ts": self.deadline_ts,
            "players_order": self.players_order,
            "room_id": self.room_id,
            "version": self.version,
        }


class FakeServer:
    """Serveur de test multi-rooms.

    Gère le strict minimum pour qu'un client GUI puisse :
      - voir l'index des rooms,
      - créer et rejoindre une room,
      - voir les transitions de phase et les albums,
      - aller jusqu'au reveal et END.
    """

    def __init__(self, phase_duration_s: int = 5):
        self.phase_duration_s = phase_duration_s
        self.rooms: Dict[str, FakeRoom] = {}
        self.global_lock = threading.Lock()
        self.index_version = 0

        # Configuration LWT serveur globale.
        will_payload = {
            "status": PresenceStatus.OFFLINE.value,
            "pseudo": SERVER_PSEUDO,
            "ts": 0,
        }
        self.mqtt = GarticMqttClient(
            client_id="fake-server",
            will_topic=topics.t_server_presence(),
            will_payload=will_payload,
            will_qos=1,
            will_retain=True,
        )

        # Souscriptions globales (wildcards pour suivre toutes les rooms).
        self.mqtt.on_message_for(
            topics.t_rooms_create(), self.on_rooms_create, qos=1,
        )
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/presence/+",
            self.on_player_presence, qos=1,
        )
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/ready/+",
            self.on_player_ready, qos=1,
        )
        self.mqtt.on_message_for(
            f"{topics.PREFIX}/rooms/+/submissions/round/+/+",
            self.on_submission, qos=1,
        )

        self.mqtt.on_ready(self.on_connected)

    # -----------------------------------------------------------------------
    # Connexion + publication initiale
    # -----------------------------------------------------------------------

    def on_connected(self) -> None:
        """Appelé à chaque (re)connexion réussie."""
        log.info("Connecté au broker")
        # Présence serveur online.
        self.mqtt.publish_json(
            topics.t_server_presence(),
            {
                "status": PresenceStatus.ONLINE.value,
                "pseudo": SERVER_PSEUDO,
                "ts": int(time.time()),
            },
            qos=1, retain=True,
        )
        # Republie l'index actuel (même s'il est vide au boot, ça écrase un
        # éventuel ancien retained mal nettoyé).
        self.publish_index()

    def publish_index(self) -> None:
        """Publie le rooms-index complet."""
        with self.global_lock:
            self.index_version += 1
            payload = {
                "rooms": [r.to_index_entry() for r in self.rooms.values()],
                "version": self.index_version,
            }
        self.mqtt.publish_json(
            topics.t_rooms_index(), payload, qos=1, retain=True,
        )
        log.info("Index publié (version=%s, %d rooms)",
                 payload["version"], len(payload["rooms"]))

    # -----------------------------------------------------------------------
    # Création de room
    # -----------------------------------------------------------------------

    def on_rooms_create(self, topic: str, payload: dict, retain: bool) -> None:
        if not isinstance(payload, dict):
            log.warning("Payload rooms-create invalide: %r", payload)
            return
        room_id = payload.get("room_id")
        name = payload.get("name")
        if not room_id or not name:
            log.warning("Payload rooms-create incomplet: %r", payload)
            return

        with self.global_lock:
            if room_id in self.rooms:
                # Collision : on log et on ignore (décision actée).
                log.warning(
                    "Collision: room_id=%s déjà existante (name=%r), "
                    "création ignorée pour name=%r",
                    room_id, self.rooms[room_id].name, name,
                )
                return
            room = FakeRoom(room_id, name)
            self.rooms[room_id] = room

        log.info("Room créée: id=%s name=%r", room_id, name)
        # Publie le state initial de la room (LOBBY).
        self.publish_state(room)
        # Met à jour l'index.
        self.publish_index()

    # -----------------------------------------------------------------------
    # Présence joueurs
    # -----------------------------------------------------------------------

    def on_player_presence(self, topic: str, payload: dict, retain: bool) -> None:
        # Topic format: <prefix>/rooms/<room_id>/presence/<pseudo>
        parts = topic.split("/")
        try:
            room_id = parts[-3]
            pseudo = parts[-1]
        except IndexError:
            return
        if not isinstance(payload, dict):
            return

        room = self.rooms.get(room_id)
        if room is None:
            log.debug("Présence sur room inconnue %s, ignorée", room_id)
            return

        status = payload.get("status")
        with room.lock:
            was_online = pseudo in room.online_players
            if status == PresenceStatus.ONLINE.value:
                room.online_players.add(pseudo)
                if not was_online:
                    log.info("[%s] %s ONLINE (%d en ligne)",
                             room_id, pseudo, len(room.online_players))
            elif status == PresenceStatus.OFFLINE.value:
                room.online_players.discard(pseudo)
                room.ready_players.discard(pseudo)
                if was_online:
                    log.info("[%s] %s OFFLINE (%d en ligne)",
                             room_id, pseudo, len(room.online_players))

            # Si la room est vide → suppression.
            if not room.online_players:
                self._delete_room_locked(room)
                return

        # Mise à jour de l'index pour refléter le nouveau n_players.
        self.publish_index()

        # Si on est en LOBBY et que les conditions de démarrage sont réunies,
        # on lance la partie.
        if room.phase == Phase.LOBBY.value:
            self.maybe_start_game(room)

    def _delete_room_locked(self, room: FakeRoom) -> None:
        """Supprime une room et nettoie tous ses retained.

        Doit être appelée alors que room.lock est tenu.
        """
        log.info("Room %s vide, suppression", room.room_id)
        # Annule un timer en cours.
        if room.phase_timer:
            room.phase_timer.cancel()

        # Clear de tous les retained de cette room.
        # On n'a pas la liste exacte des albums et submissions publiés, donc
        # on fait un best-effort sur les structures connues.
        self.mqtt.clear_retained(topics.t_state(room.room_id))
        self.mqtt.clear_retained(topics.t_reveal_current(room.room_id))
        # On efface aussi notre propre presence/<pseudo> retained de chaque
        # joueur connu (best-effort : on connaît les pseudos vus).
        # Les vrais retained presence/<pseudo> sont publiés par les clients,
        # mais on peut les écraser pour un nettoyage propre côté tests.

        # On retire la room du dico.
        with self.global_lock:
            self.rooms.pop(room.room_id, None)

        # Republie l'index.
        self.publish_index()

    # -----------------------------------------------------------------------
    # Ready joueurs
    # -----------------------------------------------------------------------

    def on_player_ready(self, topic: str, payload: dict, retain: bool) -> None:
        parts = topic.split("/")
        try:
            room_id = parts[-3]
            pseudo = parts[-1]
        except IndexError:
            return
        if not isinstance(payload, dict):
            return

        room = self.rooms.get(room_id)
        if room is None:
            return

        ready = bool(payload.get("ready", False))
        with room.lock:
            if ready:
                room.ready_players.add(pseudo)
            else:
                room.ready_players.discard(pseudo)
            log.info("[%s] %s ready=%s (%d/%d prêts)",
                     room_id, pseudo, ready,
                     len(room.ready_players & room.online_players),
                     len(room.online_players))

        # Tente de démarrer la partie si on est en LOBBY.
        if room.phase == Phase.LOBBY.value:
            self.maybe_start_game(room)

    def maybe_start_game(self, room: FakeRoom) -> None:
        """Démarre la partie si conditions remplies."""
        with room.lock:
            online = room.online_players
            ready = room.ready_players & online
            if len(online) < MIN_PLAYERS:
                return
            if ready != online:
                return  # Pas tout le monde prêt
            # Conditions OK, on démarre.
            room.players_order = sorted(online)
            room.total_rounds = total_rounds_for_players(len(room.players_order))
            log.info(
                "[%s] Démarrage de la partie : %d joueurs, %d rounds",
                room.room_id, len(room.players_order), room.total_rounds,
            )
            self._transition_to_round_locked(room, 0)

    # -----------------------------------------------------------------------
    # Boucle des rounds
    # -----------------------------------------------------------------------

    def _transition_to_round_locked(self, room: FakeRoom, round_n: int) -> None:
        """Passe la room au round donné. room.lock doit être tenu."""
        room.phase = phase_for_round(round_n).value
        room.round = round_n
        room.deadline_ts = int(time.time()) + self.phase_duration_s
        # Reset des soumissions du round.
        room.submissions[round_n] = {}
        log.info("[%s] -> phase=%s round=%d (deadline dans %ds)",
                 room.room_id, room.phase, round_n, self.phase_duration_s)
        # Publie le nouveau state.
        self.publish_state(room)
        # Met à jour l'index pour refléter la nouvelle phase.
        threading.Thread(target=self.publish_index, daemon=True).start()
        # Programme le timer de fin de phase.
        if room.phase_timer:
            room.phase_timer.cancel()
        room.phase_timer = threading.Timer(
            self.phase_duration_s, self._on_phase_deadline, args=(room.room_id,),
        )
        room.phase_timer.daemon = True
        room.phase_timer.start()

    def _on_phase_deadline(self, room_id: str) -> None:
        """Appelé à la deadline d'une phase de round."""
        room = self.rooms.get(room_id)
        if room is None:
            return
        with room.lock:
            log.info("[%s] Deadline atteinte sur round %d", room_id, room.round)
            # Construit les albums du round courant.
            self._build_albums_locked(room)
            next_round = room.round + 1
            if next_round >= room.total_rounds:
                # Fin des rounds → REVEAL.
                self._transition_to_reveal_locked(room)
            else:
                self._transition_to_round_locked(room, next_round)

    def _build_albums_locked(self, room: FakeRoom) -> None:
        """Construit et publie les albums du round courant.

        Logique simplifiée pour le fake_server :
          - On regarde qui a soumis quoi.
          - On applique la rotation pour savoir quel album recevoir cette
            soumission.
          - On publie l'entrée d'album correspondante.
        """
        round_n = room.round
        sub_by_pseudo = room.submissions.get(round_n, {})
        for pseudo in room.players_order:
            album_id = album_assigned_to_player(pseudo, round_n, room.players_order)
            sub = sub_by_pseudo.get(pseudo)
            # Index de l'auteur original de cet album.
            author_index = int(album_id[1:])
            original_author = room.players_order[author_index]

            if sub is None:
                # Placeholder en cas d'absence de soumission.
                if round_n % 2 == 0:
                    entry = {
                        "album_id": album_id,
                        "round": round_n,
                        "type": SubmissionType.SENTENCE.value,
                        "content": f"(pas de soumission de {pseudo})",
                        "contributed_by": pseudo,
                        "original_author": original_author,
                    }
                else:
                    entry = {
                        "album_id": album_id,
                        "round": round_n,
                        "type": SubmissionType.DRAWING.value,
                        "strokes": [],
                        "canvas_size": [800, 600],
                        "contributed_by": pseudo,
                        "original_author": original_author,
                    }
            else:
                if sub["type"] == SubmissionType.SENTENCE.value:
                    entry = {
                        "album_id": album_id,
                        "round": round_n,
                        "type": SubmissionType.SENTENCE.value,
                        "content": sub.get("content", ""),
                        "contributed_by": pseudo,
                        "original_author": original_author,
                    }
                else:
                    entry = {
                        "album_id": album_id,
                        "round": round_n,
                        "type": SubmissionType.DRAWING.value,
                        "strokes": sub.get("strokes", []),
                        "canvas_size": sub.get("canvas_size", [800, 600]),
                        "contributed_by": pseudo,
                        "original_author": original_author,
                    }
            self.mqtt.publish_json(
                topics.t_album(room.room_id, album_id, round_n),
                entry, qos=1, retain=True,
            )
        log.info("[%s] Albums round %d publiés", room.room_id, round_n)

    # -----------------------------------------------------------------------
    # Soumissions joueurs
    # -----------------------------------------------------------------------

    def on_submission(self, topic: str, payload: dict, retain: bool) -> None:
        # Topic: <prefix>/rooms/<room_id>/submissions/round/<n>/<pseudo>
        parts = topic.split("/")
        try:
            room_id = parts[-5]
            round_n = int(parts[-2])
            pseudo = parts[-1]
        except (IndexError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        room = self.rooms.get(room_id)
        if room is None:
            return

        with room.lock:
            if room.round != round_n:
                log.debug("[%s] Soumission de %s pour round %d ignorée (round actuel=%d)",
                          room_id, pseudo, round_n, room.round)
                return
            room.submissions.setdefault(round_n, {})[pseudo] = payload
            n_subs = len(room.submissions[round_n])
            n_expected = len(room.players_order)
            log.info("[%s] Soumission de %s round=%d (%d/%d)",
                     room_id, pseudo, round_n, n_subs, n_expected)
            # Si tout le monde a soumis avant la deadline, on accélère.
            if n_subs >= n_expected:
                if room.phase_timer:
                    room.phase_timer.cancel()
                self._build_albums_locked(room)
                next_round = room.round + 1
                if next_round >= room.total_rounds:
                    self._transition_to_reveal_locked(room)
                else:
                    self._transition_to_round_locked(room, next_round)

    # -----------------------------------------------------------------------
    # Phase REVEAL
    # -----------------------------------------------------------------------

    def _transition_to_reveal_locked(self, room: FakeRoom) -> None:
        """Bascule en phase REVEAL."""
        room.phase = Phase.REVEAL.value
        room.deadline_ts = 0
        log.info("[%s] -> phase=REVEAL", room.room_id)
        self.publish_state(room)
        threading.Thread(target=self.publish_index, daemon=True).start()
        # Lance le défilement des albums dans un thread.
        if room.phase_timer:
            room.phase_timer.cancel()
        threading.Thread(target=self._reveal_loop, args=(room.room_id,), daemon=True).start()

    def _reveal_loop(self, room_id: str) -> None:
        """Fait défiler tous les albums étape par étape."""
        room = self.rooms.get(room_id)
        if room is None:
            return
        # Pause courte pour le fake_server (2s/étape au lieu de 6s).
        step_pause = 2.0
        for album_index in range(len(room.players_order)):
            album_id = album_id_for_index(album_index)
            for step in range(room.total_rounds):
                self.mqtt.publish_json(
                    topics.t_reveal_current(room_id),
                    {
                        "album_id": album_id,
                        "step": step,
                        "total_steps": room.total_rounds,
                        "finished": False,
                    },
                    qos=1, retain=True,
                )
                time.sleep(step_pause)
        # Fin du reveal.
        self.mqtt.publish_json(
            topics.t_reveal_current(room_id),
            {
                "album_id": "",
                "step": 0,
                "total_steps": room.total_rounds,
                "finished": True,
            },
            qos=1, retain=True,
        )
        # Transition vers END.
        room2 = self.rooms.get(room_id)
        if room2 is None:
            return
        with room2.lock:
            room2.phase = Phase.END.value
            log.info("[%s] -> phase=END", room_id)
            self.publish_state(room2)
        threading.Thread(target=self.publish_index, daemon=True).start()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def publish_state(self, room: FakeRoom) -> None:
        """Publie le state retained d'une room."""
        # NB: room.lock peut être tenu ou pas — la sérialisation incrémente
        # version, ce qui est sûr car version est local à la room.
        payload = room.to_state_payload()
        self.mqtt.publish_json(
            topics.t_state(room.room_id), payload, qos=1, retain=True,
        )

    # -----------------------------------------------------------------------
    # Lancement
    # -----------------------------------------------------------------------

    def run(self) -> None:
        log.info("Lancement du fake_server (durée phase=%ds)", self.phase_duration_s)
        self.mqtt.connect_and_loop()
        if not self.mqtt.wait_until_connected(timeout_s=10):
            log.error("Timeout de connexion broker")
            return
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Arrêt demandé, déconnexion propre")
            # Présence serveur offline.
            self.mqtt.publish_json(
                topics.t_server_presence(),
                {
                    "status": PresenceStatus.OFFLINE.value,
                    "pseudo": SERVER_PSEUDO,
                    "ts": int(time.time()),
                },
                qos=1, retain=True,
            )
            time.sleep(0.5)
            self.mqtt.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake server Gartic Phone (multi-rooms).")
    parser.add_argument(
        "--phase-duration", type=int, default=5,
        help="Durée de chaque phase de round en secondes (défaut: 5)",
    )
    args = parser.parse_args()
    FakeServer(phase_duration_s=args.phase_duration).run()


if __name__ == "__main__":
    main()
