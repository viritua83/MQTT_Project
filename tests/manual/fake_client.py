"""
Faux client en console — utilisé pour tester le serveur sans GUI.

Usage :
    python -m tests.manual.fake_client <pseudo> <room_id>

Le faux client :
  - Se connecte avec LWT correcte.
  - Publie sa présence online.
  - Affiche les transitions de state qu'il reçoit.
  - Soumet automatiquement une phrase ou un dessin bidon
    quand la phase l'exige.
  - Affiche les albums reçus pendant le reveal.

Permet de simuler une partie complète en lançant 3 instances en parallèle.
"""

import argparse
import logging
import random
import sys
import time

from shared import topics
from shared.mqtt_client import GarticMqttClient
from shared.protocol import (
    Phase, PresenceStatus, SubmissionType,
    phase_for_round,
)
from shared.rotation import album_assigned_to_player


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fake_client")


class FakeClient:
    def __init__(self, pseudo: str, room_id: str):
        self.pseudo = pseudo
        self.room_id = room_id
        self.current_state = None      # Dernier StatePayload reçu
        self.last_round_submitted = -1  # Pour ne pas re-soumettre

        self.mqtt = GarticMqttClient(
            client_id=f"fake-{pseudo}-{room_id}",
            will_topic=topics.t_player_presence(room_id, pseudo),
            will_payload={
                "status": PresenceStatus.OFFLINE.value,
                "pseudo": pseudo,
                "ts": 0,
            },
            will_qos=1,
            will_retain=True,
        )

        # Handlers
        self.mqtt.on_message_for(topics.t_state(room_id), self.on_state, qos=1)
        self.mqtt.on_message_for(
            topics.sub_all_albums(room_id), self.on_album, qos=1
        )
        self.mqtt.on_message_for(
            topics.t_reveal_current(room_id), self.on_reveal, qos=1
        )

        self.mqtt.on_ready(self.on_ready)

    # ----- Hooks -----

    def on_ready(self):
        log.info("Connecté, je publie ma présence ONLINE")
        self.mqtt.publish_json(
            topics.t_player_presence(self.room_id, self.pseudo),
            {
                "status": PresenceStatus.ONLINE.value,
                "pseudo": self.pseudo,
                "ts": int(time.time()),
            },
            qos=1,
            retain=True,
        )
        # Pour les tests, on se déclare prêt automatiquement.
        # Avec 3 fake_clients lancés, la partie démarre toute seule.
        log.info("Je me déclare PRÊT automatiquement")
        self.mqtt.publish_json(
            topics.t_player_ready(self.room_id, self.pseudo),
            {
                "ready": True,
                "pseudo": self.pseudo,
                "ts": int(time.time()),
            },
            qos=1,
            retain=True,
        )

    def on_state(self, topic, payload, retain):
        prev_phase = (self.current_state or {}).get("phase")
        self.current_state = payload
        new_phase = payload["phase"]
        log.info(
            "STATE: phase=%s round=%s deadline=%s retain=%s",
            new_phase, payload["round"], payload["deadline_ts"], retain,
        )
        if new_phase != prev_phase:
            self.maybe_submit()

    def on_album(self, topic, payload, retain):
        log.info("ALBUM reçu sur %s : type=%s", topic, payload.get("type"))

    def on_reveal(self, topic, payload, retain):
        log.info(
            "REVEAL en cours : album=%s step=%s/%s",
            payload.get("album_id"), payload.get("step"), payload.get("total_steps"),
        )

    # ----- Logique métier minimale -----

    def maybe_submit(self):
        """Soumet automatiquement quelque chose si la phase l'exige."""
        s = self.current_state
        if s is None:
            return
        phase = s["phase"]
        round_n = s["round"]
        if phase not in (Phase.WRITE.value, Phase.DRAW.value, Phase.GUESS.value):
            return
        if round_n == self.last_round_submitted:
            return
        self.last_round_submitted = round_n

        # Décide quel type de soumission selon la phase
        if phase == Phase.WRITE.value:
            payload = self._fake_sentence(round_n)
        elif phase == Phase.DRAW.value:
            payload = self._fake_drawing(round_n)
        else:  # GUESS
            payload = self._fake_sentence(round_n)

        topic = topics.t_submission(self.room_id, round_n, self.pseudo)
        log.info("Soumission round=%s type=%s", round_n, payload["type"])
        self.mqtt.publish_json(topic, payload, qos=1, retain=True)

    def _fake_sentence(self, round_n):
        return {
            "type": SubmissionType.SENTENCE.value,
            "round": round_n,
            "author": self.pseudo,
            "ts": int(time.time()),
            "content": f"phrase de {self.pseudo} au round {round_n}",
        }

    def _fake_drawing(self, round_n):
        # Un "dessin" minimaliste : 2-3 traits aléatoires.
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

    # ----- Boucle -----

    def run(self):
        self.mqtt.connect_and_loop()
        if not self.mqtt.wait_until_connected(timeout_s=10):
            log.error("Timeout de connexion")
            sys.exit(1)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Arrêt demandé, déconnexion propre")
            # Publication explicite de offline avant de se déconnecter
            # (sinon la LWT ne sera pas envoyée pour une déconnexion propre)
            self.mqtt.publish_json(
                topics.t_player_presence(self.room_id, self.pseudo),
                {
                    "status": PresenceStatus.OFFLINE.value,
                    "pseudo": self.pseudo,
                    "ts": int(time.time()),
                },
                qos=1,
                retain=True,
            )
            time.sleep(0.5)
            self.mqtt.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pseudo")
    parser.add_argument("room_id")
    args = parser.parse_args()

    FakeClient(args.pseudo, args.room_id).run()


if __name__ == "__main__":
    main()
