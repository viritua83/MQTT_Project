import time
from shared.mqtt_client import GarticMqttClient
from shared import topics

class NetworkManager:
    def __init__(self, app):
        self.app = app
        self.state = app.state
        self.client = None

    def connect_menu(self):
        if self.client:
            self.client.disconnect()
            time.sleep(0.2)

        client_id = f"client-menu-{int(time.time())}"

        self.client = GarticMqttClient(client_id=client_id)

        self.client.on_message_for(topics.t_rooms_index(), self._on_rooms_index)
        self.client.on_message_for(topics.t_server_presence(), self._on_server_presence)

        self.client.connect_and_loop()

    def enter_room(self, room_id, pseudo):
        if self.client:
            self.client.disconnect()
            time.sleep(0.5)

        self.state.room_id = room_id
        self.state.pseudo = pseudo

        client_id = f"client-{pseudo}-{room_id}"
        will_topic = topics.t_player_presence(room_id, pseudo)
        will_payload = {"status": "offline", "pseudo": pseudo, "ts": int(time.time())}

        self.client = GarticMqttClient(
            client_id=client_id,
            will_topic=will_topic,
            will_payload=will_payload,
            will_retain=True
        )

        self.client.on_message_for(topics.sub_all_player_presence(room_id), self._on_presence)
        self.client.on_message_for(topics.sub_all_player_ready(room_id), self._on_player_ready)
        self.client.on_message_for(topics.sub_all_albums(room_id), self._on_album)
        self.client.on_message_for(topics.t_state(room_id), self._on_state)
        self.client.on_message_for(topics.t_reveal_current(room_id), self._on_reveal)

        self.client.on_ready(self._publish_online)
        self.client.connect_and_loop()

    def _publish_online(self):
        topic = topics.t_player_presence(self.state.room_id, self.state.pseudo)
        self.client.publish_json(topic, {"status": "online", "pseudo": self.state.pseudo, "ts": int(time.time())}, retain=True)

    def _on_server_presence(self, topic, payload, retain):
        if not payload: return
        status = payload.get("status")
        self.state.server_online = (status == "online")
        if hasattr(self.app.current_screen, 'update_server_status'):
            self.app.after(0, self.app.current_screen.update_server_status)

    def _on_rooms_index(self, topic, payload, retain):
        if not payload: return
        self.state.available_rooms = payload.get("rooms", [])
        if hasattr(self.app.current_screen, 'update_rooms_list'):
            self.app.after(0, self.app.current_screen.update_rooms_list)

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

    def _on_player_ready(self, topic, payload, retain):
        if not payload: return
        pseudo = payload.get("pseudo")
        is_ready = payload.get("ready")

        if is_ready and pseudo not in self.state.ready_players:
            self.state.ready_players.append(pseudo)
        elif not is_ready and pseudo in self.state.ready_players:
            self.state.ready_players.remove(pseudo)

        if hasattr(self.app.current_screen, 'update_players_list'):
            self.app.after(0, self.app.current_screen.update_players_list)

    def _on_album(self, topic, payload, retain):
        if not payload: return
        album_id = payload.get("album_id")
        round_n = payload.get("round")
        
        if album_id and round_n is not None:
            if album_id not in self.state.albums:
                self.state.albums[album_id] = {}
            self.state.albums[album_id][round_n] = payload

    def _on_state(self, topic, payload, retain):
        if not payload: return
        
        self.state.phase = payload.get("phase", self.state.phase)
        self.state.round = payload.get("round", self.state.round)
        self.state.total_rounds = payload.get("total_rounds", self.state.total_rounds)
        self.state.deadline_ts = payload.get("deadline_ts", self.state.deadline_ts)
        self.state.players_order = payload.get("players_order", self.state.players_order)
        self.state.version = payload.get("version", self.state.version)
        
        if hasattr(self.app, 'current_screen'):
             current_phase = self.app.current_screen.__class__.__name__.replace("Screen", "").upper()
             if self.state.phase != current_phase:
                 if self.state.phase == "LOBBY" and current_phase == "MENU":
                     pass
                 else:
                     self.app.after(0, lambda: self.app.show_screen(self.state.phase))

    def _on_reveal(self, topic, payload, retain):
        if not payload:
            return
        self.state.reveal_current = payload 
        if hasattr(self.app.current_screen, 'on_reveal_received'):
            self.app.after(0, self.app.current_screen.on_reveal_received)