import time
from shared.mqtt_client import GarticMqttClient
from shared import topics

class NetworkManager:
    def __init__(self, app):
        self.app = app
        self.state = app.state
        self.client = None

    def connect(self):
        client_id = f"client-{self.state.pseudo}-{self.state.room_id}"
        will_topic = topics.t_player_presence(self.state.room_id, self.state.pseudo)
        will_payload = {"status": "offline", "pseudo": self.state.pseudo, "ts": int(time.time())}

        self.client = GarticMqttClient(
            client_id=client_id,
            will_topic=will_topic,
            will_payload=will_payload,
            will_retain=True
        )

        self.client.on_message_for(
            topics.sub_all_player_presence(self.state.room_id),
            self._on_presence
        )
        self.client.on_message_for(
            topics.t_state(self.state.room_id),
            self._on_state
        )

        self.client.on_ready(self._publish_online)
        self.client.connect_and_loop()

    def _publish_online(self):
        topic = topics.t_player_presence(self.state.room_id, self.state.pseudo)
        self.client.publish_json(topic, {"status": "online", "pseudo": self.state.pseudo, "ts": int(time.time())}, retain=True)

    def _on_presence(self, topic, payload, retain):
        if not payload: return
        pseudo = payload.get("pseudo")
        status = payload.get("status")

        if status == "online" and pseudo not in self.state.players:
            self.state.players.append(pseudo)
        elif status == "offline" and pseudo in self.state.players:
            self.state.players.remove(pseudo)

        if hasattr(self.app.current_screen, 'update_players_list'):
            self.app.after(0, self.app.current_screen.update_players_list)

    def _on_state(self, topic, payload, retain):
        if not payload: return
        phase = payload.get("phase")
        if phase and phase != self.state.phase:
            self.state.phase = phase
            self.app.after(0, lambda: self.app.show_screen(phase))