from server.room_controller import RoomController
from shared.protocol import Phase

# Setup
r = RoomController(room_id="abc123", name="test")

# Cas 1 : pas assez de joueurs
r.add_player("alice")
r.add_player("bob")
r.set_ready("alice", True)
r.set_ready("bob", True)
assert not r.can_start(), "2 joueurs < MIN_PLAYERS=3"

# Cas 2 : assez de joueurs mais pas tous prêts
r.add_player("charlie")
assert not r.can_start(), "charlie pas prêt"

# Cas 3 : tous prêts
r.set_ready("charlie", True)
assert r.can_start(), "should start"

# Cas 4 : démarrage
r.start_game()
assert r.phase == Phase.WRITE
assert r.players_order == ["alice", "bob", "charlie"]
assert r.total_rounds == 3
assert r.deadline_ts > 0
assert r.ready_players == set()

# Cas 5 : on ne peut plus changer le ready après démarrage
assert not r.set_ready("alice", True)

# Cas 6 : un joueur qui se déconnecte est retiré du ready
r2 = RoomController(room_id="def", name="test2")
r2.add_player("alice")
r2.add_player("bob")
r2.add_player("charlie")
r2.set_ready("alice", True)
r2.set_ready("bob", True)
r2.set_ready("charlie", True)
assert r2.can_start()
r2.remove_player("charlie")
assert not r2.can_start(), "charlie offline ne doit plus compter comme prêt"
assert "charlie" not in r2.ready_players

print("✅ Tous les tests passent")