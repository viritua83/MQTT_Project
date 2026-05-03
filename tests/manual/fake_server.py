# import argparse
# import logging
# import time

# from shared import topics
# from shared.mqtt_client import GarticMqttClient
# from shared.protocol import Phase, PresenceStatus, SubmissionType
# from shared.rotation import album_id_for_index

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [fake_server] %(message)s",
#     datefmt="%H:%M:%S",
# )
# log = logging.getLogger("fake_server")

# FAKE_PLAYERS = ["alice", "bob", "charlie"]

# class FakeServer:
#     def __init__(self, room_id: str):
#         self.room_id = room_id
#         self.version = 0
#         self.mqtt = GarticMqttClient(
#             client_id=f"fake-server-{room_id}",
#             will_topic=topics.t_server_presence(),
#             will_payload={"status": "offline", "pseudo": "__server__", "ts": 0},
#             will_qos=1,
#             will_retain=True,
#         )
#         self.mqtt.on_ready(self.on_ready)

#     def on_ready(self):
#         log.info("Connecté, publication présence serveur online")
#         self.mqtt.publish_json(
#             topics.t_server_presence(),
#             {"status": "online", "pseudo": "__server__", "ts": int(time.time())},
#             qos=1, retain=True,
#         )

#         rooms_payload = {
#             "rooms": [{
#                 "id": self.room_id,
#                 "name": "Salle de Test UI",
#                 "n_players": len(FAKE_PLAYERS),
#                 "phase": "LOBBY"
#             }],
#             "version": 1
#         }
#         self.mqtt.publish_json(topics.t_rooms_index(), rooms_payload, qos=1, retain=True)

#         for p in FAKE_PLAYERS:
#             self.mqtt.publish_json(
#                 topics.t_player_presence(self.room_id, p),
#                 {"status": "online", "pseudo": p, "ts": int(time.time())},
#                 qos=1, retain=True,
#             )

#     def publish_state(self, phase: Phase, round_n: int, deadline_offset_s: int = 0):
#         self.version += 1
#         deadline = int(time.time()) + deadline_offset_s if deadline_offset_s else 0
#         payload = {
#             "phase": phase.value,
#             "round": round_n,
#             "total_rounds": len(FAKE_PLAYERS),
#             "deadline_ts": deadline,
#             "players_order": FAKE_PLAYERS,
#             "room_id": self.room_id,
#             "version": self.version,
#         }
#         log.info("STATE -> %s round=%s", phase.value, round_n)
#         self.mqtt.publish_json(topics.t_state(self.room_id), payload, qos=1, retain=True)

#     def publish_fake_album(self, album_idx: int, round_n: int):
#         is_drawing = round_n % 2 == 1
#         album_id = album_id_for_index(album_idx)
#         if is_drawing:
#             payload = {
#                 "album_id": album_id,
#                 "round": round_n,
#                 "type": SubmissionType.DRAWING.value,
#                 "strokes": [
#                     {"color": "#000000", "width": 3, "points": [[100, 100], [200, 150], [300, 200], [350, 250]]},
#                     {"color": "#ff0000", "width": 5, "points": [[400, 300], [450, 320], [500, 350]]},
#                 ],
#                 "canvas_size": [800, 600],
#                 "contributed_by": FAKE_PLAYERS[(album_idx + round_n) % len(FAKE_PLAYERS)],
#                 "original_author": FAKE_PLAYERS[album_idx],
#             }
#         else:
#             payload = {
#                 "album_id": album_id,
#                 "round": round_n,
#                 "type": SubmissionType.SENTENCE.value,
#                 "content": f"phrase de l'album {album_id} au round {round_n}",
#                 "contributed_by": FAKE_PLAYERS[(album_idx + round_n) % len(FAKE_PLAYERS)],
#                 "original_author": FAKE_PLAYERS[album_idx],
#             }
#         self.mqtt.publish_json(
#             topics.t_album(self.room_id, album_id, round_n),
#             payload, qos=1, retain=True,
#         )

#     def publish_reveal(self, album_idx: int, step: int, total: int, finished: bool = False):
#         self.mqtt.publish_json(
#             topics.t_reveal_current(self.room_id),
#             {
#                 "album_id": album_id_for_index(album_idx),
#                 "step": step,
#                 "total_steps": total,
#                 "finished": finished,
#             },
#             qos=1, retain=True,
#         )

#     def scenario_lobby(self):
#         self.publish_state(Phase.LOBBY, 0)
#         log.info("LOBBY perpétuel, Ctrl+C pour stopper")

#     def scenario_write(self, delay=3):
#         self.publish_state(Phase.LOBBY, 0)
#         time.sleep(delay)
#         self.publish_state(Phase.WRITE, 0, deadline_offset_s=45)

#     def scenario_draw(self, delay=3):
#         self.scenario_write(delay)
#         time.sleep(delay)
#         for i in range(len(FAKE_PLAYERS)):
#             self.publish_fake_album(i, 0)
#         self.publish_state(Phase.DRAW, 1, deadline_offset_s=90)

#     def scenario_guess(self, delay=3):
#         self.scenario_draw(delay)
#         time.sleep(delay)
#         for i in range(len(FAKE_PLAYERS)):
#             self.publish_fake_album(i, 1)
#         self.publish_state(Phase.GUESS, 2, deadline_offset_s=45)

#     def scenario_reveal(self, delay=3):
#         self.scenario_guess(delay)
#         time.sleep(delay)
#         total = len(FAKE_PLAYERS)
#         for round_n in range(total):
#             for i in range(len(FAKE_PLAYERS)):
#                 self.publish_fake_album(i, round_n)
#         self.publish_state(Phase.REVEAL, total - 1)
#         for i in range(len(FAKE_PLAYERS)):
#             for step in range(total):
#                 self.publish_reveal(i, step, total)
#                 time.sleep(2)
#         self.publish_reveal(len(FAKE_PLAYERS) - 1, total - 1, total, finished=True)
#         self.publish_state(Phase.END, total - 1)

#     def run(self, scenario: str):
#         self.mqtt.connect_and_loop()
#         if not self.mqtt.wait_until_connected(timeout_s=10):
#             log.error("Timeout connexion")
#             return

#         scenarios = {
#             "lobby": self.scenario_lobby,
#             "write": self.scenario_write,
#             "draw": self.scenario_draw,
#             "guess": self.scenario_guess,
#             "reveal": self.scenario_reveal,
#             "full": self.scenario_reveal,
#         }
#         try:
#             scenarios[scenario]()
#             log.info("Scénario terminé, je reste connecté (Ctrl+C pour stopper)")
#             while True:
#                 time.sleep(1)
#         except KeyboardInterrupt:
#             log.info("Arrêt")
#             self.mqtt.disconnect()

# def main():
#     parser = argparse.ArgumentParser()
#     parser.add_argument("room_id")
#     parser.add_argument("--scenario", default="lobby", choices=["lobby", "write", "draw", "guess", "reveal", "full"])
#     args = parser.parse_args()
#     FakeServer(args.room_id).run(args.scenario)

# if __name__ == "__main__":
#     main()