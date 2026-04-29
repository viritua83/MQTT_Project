"""
Orchestrateur principal du serveur arbitre Gartic Phone.

Responsabilités à l'étape 1 :
  - Connexion au broker MQTT avec une LWT serveur globale.
  - Souscription aux wildcards globaux pour observer toutes les rooms.
  - Publication initiale de server/presence (online) et rooms-index (vide).
  - Détection (via log) des rooms existantes au démarrage — la reprise
    réelle viendra à l'étape 2 quand on aura les RoomController.
  - Arrêt propre : publication explicite de server/presence (offline) avant
    la déconnexion.

Étapes suivantes (déjà préparées dans la structure) :
  - Étape 2 : création/suppression de rooms (handler de rooms-create,
    dictionnaire rooms: Dict[str, RoomController], maintenance de
    rooms-index).
  - Étape 3+ : démarrage automatique des parties, boucle des rounds, etc.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from shared import topics
from shared.mqtt_client import GarticMqttClient
from shared.protocol import PresenceStatus

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
        # Vide à l'étape 1, rempli à l'étape 2 quand on traitera rooms-create.
        # On le déclare quand même ici pour avoir la structure en place.
        self.rooms: Dict[str, Any] = {}

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

        À l'étape 1 on souscrit aux wildcards globaux, mais la plupart des
        handlers ne font que loguer pour vérifier que le routage marche.
        Ils seront remplis à mesure qu'on avance dans les étapes.
        """
        # rooms-index retained : utile pour la reprise au démarrage.
        # On veut savoir si des rooms existaient avant qu'on crash.
        self.mqtt.on_message_for(
            topics.t_rooms_index(),
            self._on_rooms_index_retained,
            qos=1,
        )

        # rooms-create : déclenché par les clients pour créer une room.
        # QoS 0 par contrat, mais on le re-déclare ici par cohérence avec
        # les autres souscriptions.
        self.mqtt.on_message_for(
            topics.t_rooms_create(),
            self._on_rooms_create,
            qos=0,
        )

        # Wildcards globaux pour observer tous les évènements joueurs
        # de toutes les rooms. À l'étape 1 on ne fait que loguer.
        # On extrait le room_id du topic au moment du routage.
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
        handlers (`_on_rooms_index_retained`, `_on_player_presence`, etc.)
        qui s'autorisent à mettre à jour les structures internes mais
        s'interdisent de PUBLIER quoi que ce soit (le drapeau
        self._recovery_done n'est pas encore set).
        """
        time.sleep(RECOVERY_WINDOW_S)
        self._recovery_done.set()
        log.info("Fenêtre de reprise terminée")

        # Maintenant qu'on a (théoriquement) reçu les retained existants,
        # on annonce notre présence et on publie l'index.
        self._publish_server_online()
        self._publish_rooms_index()

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

        À l'étape 1 self.rooms est toujours vide donc on publie une liste
        vide. À l'étape 2+, on listera les rooms gérées avec leurs métadonnées.
        """
        with self._rooms_lock:
            self._index_version += 1
            payload = {
                "rooms": [
                    # À l'étape 2, ce sera : room.to_index_entry()
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

    # ------------------------------------------------------------------
    # Handlers de messages (étape 1 : simples logs)
    # ------------------------------------------------------------------

    def _on_rooms_index_retained(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Reçu pendant la fenêtre de reprise OU après nos propres publishes.

        À l'étape 1 :
          - Si reçu PENDANT la reprise et que le payload contient des rooms,
            on le logue (information utile pour la démo : "tiens, il y avait
            des rooms avant le crash").
          - Si reçu APRÈS la reprise, c'est juste l'écho de notre propre
            publish, on ignore.
        """
        if self._recovery_done.is_set():
            # C'est l'écho de notre propre publish, rien à faire.
            return

        if not isinstance(payload, dict):
            return
        existing = payload.get("rooms", [])
        if existing:
            log.info(
                "Rooms existantes détectées au démarrage : %s "
                "(reprise complète prévue à l'étape 2)",
                [r.get("id") for r in existing],
            )
        else:
            log.info("Aucune room existante (index vide ou absent)")

    def _on_rooms_create(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Demande de création de room (étape 2).

        À l'étape 1 on ignore — on logue pour montrer que le routage marche.
        """
        log.info("rooms-create reçu : %r (ignoré à l'étape 1)", payload)

    def _on_player_presence(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """Présence d'un joueur dans une room (étape 2+).

        À l'étape 1 on ne fait que loguer pour valider que les wildcards
        fonctionnent et que le routage par room_id sera possible.
        """
        # Format du topic : <PREFIX>/rooms/<room_id>/presence/<pseudo>
        room_id, pseudo = self._extract_room_and_user(topic)
        if room_id is None:
            return
        status = payload.get("status") if isinstance(payload, dict) else None
        log.info(
            "[%s] presence/%s = %s (retain=%s)",
            room_id, pseudo, status, retain,
        )

    def _on_player_ready(
        self,
        topic: str,
        payload: Any,
        retain: bool,
    ) -> None:
        """État ready d'un joueur (étape 3)."""
        room_id, pseudo = self._extract_room_and_user(topic)
        if room_id is None:
            return
        ready = payload.get("ready") if isinstance(payload, dict) else None
        log.info(
            "[%s] ready/%s = %s (retain=%s)",
            room_id, pseudo, ready, retain,
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