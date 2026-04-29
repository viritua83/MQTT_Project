"""
Faux client en console — utilisé pour tester le serveur sans GUI.

Adapté à la nouvelle architecture (mai 2025) :
  - Serveur unique multi-rooms.
  - Index des rooms dans un seul JSON (rooms-index).
  - Création de room par publish sur rooms-create avec un room_id généré
    aléatoirement côté client (secrets.token_hex(3)).
  - LWT serveur globale, LWT joueur par room.

Usage :
    # Créer une room et la rejoindre
    python -m tests.manual.fake_client alice --create soiree

    # Rejoindre une room existante par son id
    python -m tests.manual.fake_client bob --join a3f7b2

    # Rejoindre la première room dispo dans l'index (ou attendre qu'il y en ait une)
    python -m tests.manual.fake_client charlie --join-any

Le faux client :
  - Génère son room_id si --create.
  - Configure sa LWT correctement avant d'entrer dans la room.
  - Publie online + ready=true automatiquement à la connexion à la room.
  - Affiche les transitions de state qu'il reçoit.
  - Soumet automatiquement une phrase ou un dessin bidon
    quand la phase l'exige.
  - Affiche les albums et reveals reçus.

Permet de simuler une partie complète en lançant 3 instances en parallèle.
"""

import argparse
import logging
import random
import secrets
import sys
import time
from typing import Optional

from shared import topics
from shared.mqtt_client import GarticMqttClient
from shared.protocol import (
    Phase, PresenceStatus, SubmissionType,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fake_client")


class FakeClient:
    """Client de test en console.

    Cycle de vie :
      1. Phase 'menu' : connecté sans LWT joueur, écoute rooms-index et
         server/presence pour découvrir l'état du système.
      2. Décide d'une room (création ou rejoint existante).
      3. Phase 'in_room' : reconnecté avec LWT joueur configurée, publie
         online + ready, joue la partie automatiquement.
    """

    def __init__(
        self,
        pseudo: str,
        create_name: Optional[str] = None,
        join_room_id: Optional[str] = None,
        join_any: bool = False,
    ):
        self.pseudo = pseudo
        self.create_name = create_name
        self.join_room_id = join_room_id
        self.join_any = join_any

        # État courant.
        self.room_id: Optional[str] = None
        self.room_name: Optional[str] = None
        self.current_state: Optional[dict] = None
        self.last_round_submitted = -1
        self.in_room = False

        # Mémoire du dernier index reçu (pour la recherche après création).
        self.last_index: Optional[dict] = None

        # Le client MQTT est créé sans LWT joueur en phase 'menu'.
        # Il sera recréé avec LWT au moment de rejoindre une room.
        self.mqtt: Optional[GarticMqttClient] = None
        self._connect_menu()

    # -----------------------------------------------------------------------
    # Phase 1 — connexion en mode menu (sans LWT joueur)
    # -----------------------------------------------------------------------

    def _connect_menu(self) -> None:
        """Connexion initiale, sans LWT (on n'est pas encore dans une room)."""
        log.info("[%s] Connexion en mode menu", self.pseudo)
        self.mqtt = GarticMqttClient(
            client_id=f"fake-{self.pseudo}-menu-{secrets.token_hex(2)}",
        )
        self.mqtt.on_message_for(topics.t_rooms_index(), self.on_rooms_index, qos=1)
        self.mqtt.on_message_for(topics.t_server_presence(), self.on_server_presence, qos=1)
        self.mqtt.on_ready(self.on_menu_ready)
        self.mqtt.connect_and_loop()
        if not self.mqtt.wait_until_connected(timeout_s=10):
            log.error("[%s] Timeout connexion broker", self.pseudo)
            sys.exit(1)

    def on_menu_ready(self) -> None:
        """Appelé à chaque (re)connexion en mode menu."""
        log.info("[%s] Connecté en mode menu, en attente de l'index...", self.pseudo)

    def on_rooms_index(self, topic: str, payload: dict, retain: bool) -> None:
        """Réception de l'index des rooms."""
        if not payload:
            return
        self.last_index = payload
        rooms = payload.get("rooms", [])
        log.info(
            "[%s] Index reçu (version=%s, %d rooms) %s",
            self.pseudo, payload.get("version"), len(rooms),
            [r["id"] for r in rooms],
        )

        # Si on est déjà dans une room, on ignore (l'index nous intéresse plus).
        if self.in_room:
            return

        # Logique de décision : créer, rejoindre une room précise, ou rejoindre n'importe laquelle.
        if self.create_name and self.room_id is None:
            # Première fois qu'on voit l'index : on lance la création.
            self._create_room()
        elif self.create_name and self.room_id:
            # On a déjà publié rooms-create, on cherche notre room_id.
            for r in rooms:
                if r["id"] == self.room_id:
                    log.info("[%s] Notre room %s est apparue dans l'index, on la rejoint", self.pseudo, self.room_id)
                    self.room_name = r.get("name")
                    self._enter_room(self.room_id)
                    return
        elif self.join_room_id:
            for r in rooms:
                if r["id"] == self.join_room_id:
                    self.room_id = r["id"]
                    self.room_name = r.get("name")
                    log.info("[%s] Room %s trouvée, on la rejoint", self.pseudo, self.room_id)
                    self._enter_room(self.room_id)
                    return
        elif self.join_any:
            if rooms:
                r = rooms[0]
                self.room_id = r["id"]
                self.room_name = r.get("name")
                log.info("[%s] Rejoint la room %s (auto)", self.pseudo, self.room_id)
                self._enter_room(self.room_id)

    def on_server_presence(self, topic: str, payload: dict, retain: bool) -> None:
        """Affiche les changements d'état du serveur."""
        if payload:
            log.info("[%s] Serveur: status=%s", self.pseudo, payload.get("status"))

    # -----------------------------------------------------------------------
    # Phase 2 — création de room (si demandée)
    # -----------------------------------------------------------------------

    def _create_room(self) -> None:
        """Génère un room_id et publie rooms-create."""
        self.room_id = secrets.token_hex(3)  # ex: "a3f7b2"
        log.info(
            "[%s] Création room name=%r room_id=%s",
            self.pseudo, self.create_name, self.room_id,
        )
        self.mqtt.publish_json(
            topics.t_rooms_create(),
            {"room_id": self.room_id, "name": self.create_name},
            qos=0,           # QoS 0 : best-effort, retry au timeout si besoin
            retain=False,    # Non retained : c'est un événement
        )
        # On ne fait rien d'autre ici. La suite est dans on_rooms_index :
        # quand notre room_id apparaît dans l'index, on la rejoint.
        # Si pas d'apparition après 3s, c'est un échec → on retente.
        # Pour le fake_client on simplifie : on retente une fois.
        # (Le vrai client GUI affichera une erreur à l'utilisateur.)

    # -----------------------------------------------------------------------
    # Phase 3 — entrée dans une room (reconnexion avec LWT joueur)
    # -----------------------------------------------------------------------

    def _enter_room(self, room_id: str) -> None:
        """Reconnecte avec une LWT joueur configurée pour cette room."""
        log.info("[%s] Entrée dans la room %s", self.pseudo, room_id)

        # On déconnecte le client 'menu' proprement.
        self.mqtt.disconnect()
        time.sleep(0.2)  # Laisse paho finir sa déconnexion

        # On crée un nouveau client MQTT avec :
        #  - client_id stable basé sur (pseudo, room) : essentiel pour la
        #    reprise de session (clean_session=False).
        #  - LWT joueur : si on crash, le broker publie offline pour nous.
        will_topic = topics.t_player_presence(room_id, self.pseudo)
        will_payload = {
            "status": PresenceStatus.OFFLINE.value,
            "pseudo": self.pseudo,
            "ts": 0,
        }
        self.mqtt = GarticMqttClient(
            client_id=f"fake-{self.pseudo}-{room_id}",
            will_topic=will_topic,
            will_payload=will_payload,
            will_qos=1,
            will_retain=True,
        )

        # Souscriptions in-room.
        self.mqtt.on_message_for(topics.t_state(room_id), self.on_state, qos=1)
        self.mqtt.on_message_for(
            topics.sub_all_albums(room_id), self.on_album, qos=1,
        )
        self.mqtt.on_message_for(
            topics.t_reveal_current(room_id), self.on_reveal, qos=1,
        )
        # On garde aussi un œil sur server/presence pour détecter un crash serveur.
        self.mqtt.on_message_for(
            topics.t_server_presence(), self.on_server_presence, qos=1,
        )
        self.mqtt.on_ready(self.on_room_ready)

        self.mqtt.connect_and_loop()
        if not self.mqtt.wait_until_connected(timeout_s=10):
            log.error("[%s] Timeout reconnexion en mode room", self.pseudo)
            sys.exit(1)
        self.in_room = True

    def on_room_ready(self) -> None:
        """Appelé à chaque (re)connexion en mode in-room.

        On republie présence et ready à chaque reconnexion : si le broker
        avait perdu la session (rare avec clean_session=False mais possible),
        ça garantit qu'on est bien marqué online.
        """
        log.info("[%s] Connecté en mode room, publication online + ready", self.pseudo)
        self.mqtt.publish_json(
            topics.t_player_presence(self.room_id, self.pseudo),
            {
                "status": PresenceStatus.ONLINE.value,
                "pseudo": self.pseudo,
                "ts": int(time.time()),
            },
            qos=1, retain=True,
        )
        # Le fake_client se déclare prêt automatiquement.
        # Un vrai client attendrait que l'utilisateur clique le bouton.
        self.mqtt.publish_json(
            topics.t_player_ready(self.room_id, self.pseudo),
            {
                "ready": True,
                "pseudo": self.pseudo,
                "ts": int(time.time()),
            },
            qos=1, retain=True,
        )

    # -----------------------------------------------------------------------
    # Handlers in-room
    # -----------------------------------------------------------------------

    def on_state(self, topic: str, payload: dict, retain: bool) -> None:
        """Réception du state global de la room."""
        if not payload:
            return
        prev_phase = (self.current_state or {}).get("phase")
        self.current_state = payload
        new_phase = payload["phase"]
        log.info(
            "[%s] STATE: phase=%s round=%s/%s deadline=%s retain=%s",
            self.pseudo,
            new_phase, payload["round"], payload.get("total_rounds"),
            payload["deadline_ts"], retain,
        )
        if new_phase != prev_phase:
            self.maybe_submit()

    def on_album(self, topic: str, payload: dict, retain: bool) -> None:
        """Réception d'un album."""
        if not payload:
            return
        log.info(
            "[%s] ALBUM %s round=%s type=%s contributed_by=%s",
            self.pseudo, payload.get("album_id"), payload.get("round"),
            payload.get("type"), payload.get("contributed_by"),
        )

    def on_reveal(self, topic: str, payload: dict, retain: bool) -> None:
        """Réception du pointeur de reveal."""
        if not payload:
            return
        log.info(
            "[%s] REVEAL: album=%s step=%s/%s finished=%s",
            self.pseudo,
            payload.get("album_id"), payload.get("step"),
            payload.get("total_steps"), payload.get("finished"),
        )

    # -----------------------------------------------------------------------
    # Logique de soumission automatique
    # -----------------------------------------------------------------------

    def maybe_submit(self) -> None:
        """Soumet automatiquement quand la phase l'exige et qu'on l'a pas déjà fait."""
        s = self.current_state
        if s is None:
            return
        phase = s["phase"]
        round_n = s["round"]
        if phase not in (Phase.WRITE.value, Phase.DRAW.value, Phase.GUESS.value):
            return
        # Idempotence : on ne soumet qu'une fois par round.
        if round_n == self.last_round_submitted:
            return
        self.last_round_submitted = round_n

        if phase == Phase.WRITE.value:
            payload = self._fake_sentence(round_n)
        elif phase == Phase.DRAW.value:
            payload = self._fake_drawing(round_n)
        else:  # GUESS
            payload = self._fake_sentence(round_n)

        topic = topics.t_submission(self.room_id, round_n, self.pseudo)
        log.info(
            "[%s] Soumission round=%s type=%s",
            self.pseudo, round_n, payload["type"],
        )
        self.mqtt.publish_json(topic, payload, qos=1, retain=True)

    def _fake_sentence(self, round_n: int) -> dict:
        return {
            "type": SubmissionType.SENTENCE.value,
            "round": round_n,
            "author": self.pseudo,
            "ts": int(time.time()),
            "content": f"phrase de {self.pseudo} au round {round_n}",
        }

    def _fake_drawing(self, round_n: int) -> dict:
        n_strokes = random.randint(2, 4)
        strokes = []
        for _ in range(n_strokes):
            n_pts = random.randint(3, 8)
            points = [
                [random.randint(0, 800), random.randint(0, 600)]
                for _ in range(n_pts)
            ]
            strokes.append({
                "color": "#000000",
                "width": 3,
                "points": points,
            })
        return {
            "type": SubmissionType.DRAWING.value,
            "round": round_n,
            "author": self.pseudo,
            "ts": int(time.time()),
            "strokes": strokes,
            "canvas_size": [800, 600],
        }

    # -----------------------------------------------------------------------
    # Boucle principale + arrêt propre
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Boucle infinie, arrêt par Ctrl+C."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("[%s] Arrêt demandé, déconnexion propre", self.pseudo)
            if self.in_room and self.room_id:
                # Publication explicite de offline avant déconnexion.
                # Sans ça, le retained presence resterait à online jusqu'à
                # la LWT (qui n'arrive pas en cas de déconnexion propre).
                self.mqtt.publish_json(
                    topics.t_player_presence(self.room_id, self.pseudo),
                    {
                        "status": PresenceStatus.OFFLINE.value,
                        "pseudo": self.pseudo,
                        "ts": int(time.time()),
                    },
                    qos=1, retain=True,
                )
                time.sleep(0.5)  # Laisse paho envoyer avant de couper
            self.mqtt.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake client Gartic Phone (console).")
    parser.add_argument("pseudo", help="Pseudo du joueur")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", metavar="NAME", help="Créer une nouvelle room avec ce nom")
    group.add_argument("--join", metavar="ROOM_ID", help="Rejoindre une room par son id")
    group.add_argument("--join-any", action="store_true", help="Rejoindre la première room dispo")
    args = parser.parse_args()

    client = FakeClient(
        pseudo=args.pseudo,
        create_name=args.create,
        join_room_id=args.join,
        join_any=args.join_any,
    )
    client.run()


if __name__ == "__main__":
    main()
