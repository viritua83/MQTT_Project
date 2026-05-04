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

        # Étape 4+ : albums reçus du serveur, structure {album_id: {round_n: payload}}.
        # Alimenté par network._on_album à chaque retained albums/<id>/round/<n>.
        # Lu par DrawScreen et GuessScreen pour afficher la phrase à dessiner
        # ou le dessin à deviner.
        self.albums = {}