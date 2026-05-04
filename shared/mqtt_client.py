"""
Wrapper paho-mqtt centralisé pour le projet Gartic.

Objectifs :
  - Reconnexion automatique transparente.
  - Session persistante (clean_session=False) pour ne pas perdre de messages
    QoS 1 pendant une coupure réseau.
  - LWT configurée proprement avant connect().
  - API JSON-first : on publie et reçoit des dicts, le wrapper se charge
    de json.dumps / json.loads.
  - Découplage : le code applicatif enregistre des callbacks par topic pattern,
    sans toucher à paho directement.

Note importante : avec clean_session=False, le broker conserve aussi les
souscriptions de la session précédente. On les ré-émet quand même au moment
on_connect par sécurité (cas où une nouvelle session serait créée côté broker).
"""

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import paho.mqtt.client as paho
from paho.mqtt.client import MQTTMessage

from shared.protocol import BROKER_HOST, BROKER_PORT, KEEPALIVE_S


log = logging.getLogger("mqtt")

# Type d'un callback de message : reçoit (topic, payload_decodé, retain_flag).
MessageHandler = Callable[[str, Any, bool], None]


class GarticMqttClient:
    """Wrapper paho fournissant publish/subscribe avec sérialisation JSON.

    Usage :
        c = GarticMqttClient(client_id="alice-room1",
                             will_topic="...", will_payload={"status": "offline"})
        c.on_message_for("isen-2026-gN/gartic/rooms/room1/state", handler)
        c.connect_and_loop()
        c.publish_json("topic", {"hello": "world"}, qos=1, retain=True)
    """

    def __init__(
        self,
        client_id: str,
        will_topic: Optional[str] = None,
        will_payload: Optional[Dict[str, Any]] = None,
        will_qos: int = 1,
        will_retain: bool = True,
    ):
        # On utilise MQTT v3.1.1 par compatibilité large avec emqx public.
        # client_id doit être stable pour que clean_session=False fonctionne.
        self._client = paho.Client(
            client_id=client_id,
            clean_session=False,
            protocol=paho.MQTTv311,
        )
        self._client_id = client_id

        # Reconnexion exponentielle bornée : 1s -> 2s -> ... -> 30s max.
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        # LWT : si configurée, le broker la publiera automatiquement quand
        # il détectera notre disparition (timeout du keepalive).
        if will_topic is not None:
            self._client.will_set(
                topic=will_topic,
                payload=json.dumps(will_payload or {}),
                qos=will_qos,
                retain=will_retain,
            )

        # Table des handlers : topic exact (avec wildcards possibles) -> callback.
        # paho gère le matching wildcards via message_callback_add.
        self._handlers: List[Tuple[str, int, MessageHandler]] = []
        self._lock = threading.Lock()

        # Hooks paho.
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message_default

        self._connected_event = threading.Event()
        self._on_ready_callbacks: List[Callable[[], None]] = []

    # -----------------------------------------------------------------------
    # API publique
    # -----------------------------------------------------------------------

    def on_message_for(
        self,
        topic_filter: str,
        handler: MessageHandler,
        qos: int = 1,
    ) -> None:
        """Enregistre un handler pour un topic (peut contenir + et #).

        L'abonnement est effectif après connect_and_loop(). Si on est déjà
        connecté, l'abonnement est fait immédiatement.
        """
        with self._lock:
            self._handlers.append((topic_filter, qos, handler))
        # Wrapper qui décode le JSON et expose retain.
        self._client.message_callback_add(
            topic_filter, self._wrap_handler(handler)
        )
        if self._connected_event.is_set():
            self._client.subscribe(topic_filter, qos=qos)

    def on_ready(self, callback: Callable[[], None]) -> None:
        """Callback appelé à chaque (re)connexion réussie."""
        self._on_ready_callbacks.append(callback)

    def publish_json(
        self,
        topic: str,
        payload: Any,
        qos: int = 1,
        retain: bool = False,
    ) -> None:
        """Publie un payload sérialisé en JSON."""
        data = json.dumps(payload, separators=(",", ":"))
        info = self._client.publish(topic, data, qos=qos, retain=retain)
        # On ne bloque pas sur l'ack ici : QoS 1 garantit la livraison
        # avec la session persistante, même si on n'attend pas explicitement.
        if info.rc != paho.MQTT_ERR_SUCCESS:
            log.warning("Publish a échoué (rc=%s) sur %s", info.rc, topic)

    def publish_raw(
        self,
        topic: str,
        payload: bytes | str,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Publie un payload brut (utile pour effacer un retained avec b'')."""
        self._client.publish(topic, payload, qos=qos, retain=retain)

    def clear_retained(self, topic: str) -> None:
        """Efface un message retained sur un topic (publish vide + retain)."""
        self._client.publish(topic, payload=b"", qos=1, retain=True)

    def connect_and_loop(self) -> None:
        """Connexion + boucle réseau dans un thread de fond (non bloquant)."""
        self._client.connect_async(BROKER_HOST, BROKER_PORT, KEEPALIVE_S)
        self._client.loop_start()

    def disconnect(self) -> None:
        """Déconnexion propre (n'envoie pas la LWT)."""
        self._client.loop_stop()
        self._client.disconnect()

    def wait_until_connected(self, timeout_s: float = 10.0) -> bool:
        return self._connected_event.wait(timeout_s)

    # -----------------------------------------------------------------------
    # Hooks paho internes
    # -----------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("[%s] Connecté au broker (session_present=%s)",
                     self._client_id, flags.get("session present"))
            self._connected_event.set()
            # On se ré-abonne explicitement à tous nos topics.
            # Si la session était déjà présente, c'est redondant mais inoffensif.
            with self._lock:
                handlers = list(self._handlers)
            for topic_filter, qos, _ in handlers:
                client.subscribe(topic_filter, qos=qos)
            for cb in self._on_ready_callbacks:
                try:
                    cb()
                except Exception:
                    log.exception("on_ready callback a levé une exception")
        else:
            log.error("[%s] Connexion refusée, rc=%s", self._client_id, rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected_event.clear()
        if rc != 0:
            log.warning(
                "[%s] Déconnexion inattendue (rc=%s), paho va tenter de "
                "se reconnecter automatiquement",
                self._client_id, rc,
            )
        else:
            log.info("[%s] Déconnexion propre", self._client_id)

    def _on_message_default(self, client, userdata, msg: MQTTMessage):
        # Ce callback ne devrait être appelé que si AUCUN handler spécifique
        # ne matche. On log au cas où.
        log.debug("Message non routé sur %s", msg.topic)

    @staticmethod
    def _wrap_handler(handler: MessageHandler):
        """Convertit un handler "métier" (topic, dict, retain) en callback paho."""
        def _cb(client, userdata, msg: MQTTMessage):
            try:
                if msg.payload:
                    try:
                        payload = json.loads(msg.payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # Payload non-JSON : on passe les bytes bruts.
                        payload = msg.payload
                else:
                    payload = None
                handler(msg.topic, payload, bool(msg.retain))
            except Exception:
                log.exception("Handler a levé une exception sur %s", msg.topic)
        return _cb