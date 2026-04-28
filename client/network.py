from shared.mqtt_client import MqttClient
from shared.topics import TopicBuilder
import json

class NetworkManager:
    def __init__(self, state):
        self.state = state
        self.client = MqttClient(client_id=f"client_{state.pseudo}")

    def connect(self):
        lwt_topic = TopicBuilder.presence(self.state.room_id, self.state.pseudo)
        lwt_payload = {"status": "offline", "reason": "crash"}
        
        self.client.connect(lwt_topic, lwt_payload)
        
        self.client.subscribe(TopicBuilder.state(self.state.room_id), self._on_state_received)
        self.client.subscribe(TopicBuilder.presence(self.state.room_id, "+"), self._on_presence_received)

    def _on_state_received(self, client, userdata, msg):
        data = json.loads(msg.payload)
        self.state.phase = data.get("phase")
