from shared.protocol import Phase

class ClientState:
    def __init__(self):
        self.pseudo = ""
        self.room_id = ""
        self.is_ready = False
        self.players = []
        self.ready_players = []
        
        self.phase = Phase.LOBBY.value
        self.round = 0
        self.total_rounds = 0
        self.deadline_ts = 0
        self.players_order = []
        self.version = 0

        self.server_online = False
        self.available_rooms = []
        