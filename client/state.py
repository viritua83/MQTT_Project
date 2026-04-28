class ClientState:
    def __init__(self):
        self.pseudo = ""
        self.room_id = ""
        self.phase = "LOBBY"
        self.players = []
        self.current_album = None
        self.timer_end = 0