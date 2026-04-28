import tkinter as tk

class LoginScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="Bienvenue dans Gartic Paint", 
                 font=("Arial", 24, "bold"), fg="white", bg="#2C3E50").pack(pady=40)

        tk.Label(self, text="Ton Pseudo :", font=("Arial", 14), fg="white", bg="#2C3E50").pack(pady=5)
        self.pseudo_entry = tk.Entry(self, font=("Arial", 14), justify="center")
        self.pseudo_entry.pack(pady=5)

        tk.Label(self, text="Nom de la Salle :", font=("Arial", 14), fg="white", bg="#2C3E50").pack(pady=5)
        self.room_entry = tk.Entry(self, font=("Arial", 14), justify="center")
        self.room_entry.pack(pady=5)

        tk.Button(self, text="Rejoindre la partie", font=("Arial", 14, "bold"), 
                  bg="#27AE60", fg="white", command=self.join_game).pack(pady=30)

    def join_game(self):
        pseudo = self.pseudo_entry.get().strip()
        room = self.room_entry.get().strip()

        if pseudo and room:
            self.app.state.pseudo = pseudo
            self.app.state.room_id = room
            
            # TODO: C'est ici qu'on appellera self.app.net.connect() plus tard !
            
            self.app.show_screen("LOBBY")

class LobbyScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text=f"Lobby - Salle : {self.app.state.room_id}", 
                 font=("Arial", 20, "bold"), fg="white", bg="#2C3E50").pack(pady=20)
        
        tk.Label(self, text=f"Connecté en tant que : {self.app.state.pseudo}", 
                 font=("Arial", 14), fg="lightgray", bg="#2C3E50").pack(pady=10)

class DrawScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="white")
        
        tk.Label(self, text="Écran de dessin (en construction)", 
                 font=("Arial", 20), bg="white").pack(pady=50)