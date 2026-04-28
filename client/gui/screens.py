import tkinter as tk
from shared import topics
import time

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
            
            self.app.net.connect()
            
            self.app.show_screen("LOBBY")

class LobbyScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text=f"Lobby - Salle : {self.app.state.room_id}", 
                 font=("Arial", 20, "bold"), fg="white", bg="#2C3E50").pack(pady=20)
        
        tk.Label(self, text=f"Connecté : {self.app.state.pseudo}", 
                 font=("Arial", 12), fg="lightgray", bg="#2C3E50").pack(pady=5)

        self.players_label = tk.Label(self, text="Joueurs en ligne :\n- " + self.app.state.pseudo, 
                                      font=("Arial", 14), fg="#3498DB", bg="#2C3E50")
        self.players_label.pack(pady=20)

        self.ready_btn = tk.Button(self, text="Je suis prêt !", font=("Arial", 14, "bold"), 
                                   bg="#E67E22", fg="white", command=self.toggle_ready)
        self.ready_btn.pack(pady=30)

    def toggle_ready(self):
        self.app.state.is_ready = not self.app.state.is_ready
        
        if self.app.state.is_ready:
            self.ready_btn.config(bg="#27AE60", text="Prêt ✔ (Attente des autres...)")
        else:
            self.ready_btn.config(bg="#E67E22", text="Je suis prêt !")

        payload = {
            "ready": self.app.state.is_ready,
            "pseudo": self.app.state.pseudo,
            "ts": int(time.time())
        }
        topic = topics.t_player_ready(self.app.state.room_id, self.app.state.pseudo)
        self.app.net.client.publish_json(topic, payload, qos=1, retain=True)

    def update_players_list(self):
        """Cette fonction sera appelée par le réseau quand un joueur rejoint/quitte"""
        text = "Joueurs en ligne :\n"
        for p in self.app.state.players:
            text += f"- {p}\n"
        self.players_label.config(text=text)

class DrawScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="À toi de dessiner !", font=("Arial", 18, "bold"), 
                 fg="white", bg="#2C3E50").pack(pady=10)

        self.toolbar = tk.Frame(self, bg="gray")
        self.toolbar.pack(side="top", fill="x", padx=20)

        self.current_color = "black"
        self.colors = ["black", "red", "green", "blue", "yellow", "orange", "purple", "white"]
        
        tk.Button(self.toolbar, text="Envoyer le dessin ✔", bg="#27AE60", fg="white", 
                  font=("Arial", 10, "bold"), command=self.submit_drawing).pack(side="right", padx=10)

        tk.Button(self.toolbar, text="Effacer", command=self.clear_canvas).pack(side="right", padx=5)
        tk.Button(self.toolbar, text="Redo ↪", command=self.redo).pack(side="right", padx=2)
        tk.Button(self.toolbar, text="Undo ↩", command=self.undo).pack(side="right", padx=2)

        for color in self.colors:
            btn = tk.Button(self.toolbar, bg=color, width=3, command=lambda c=color: self.set_color(c))
            btn.pack(side="left", padx=2, pady=2)

        self.canvas = tk.Canvas(self, bg="white", width=600, height=400)
        self.canvas.pack(pady=10)

        self.last_x, self.last_y = None, None
        self.all_strokes = []
        self.redo_stack = []
        self.current_stroke_points = []

        self.canvas.bind("<Button-1>", self.start_draw)
        self.canvas.bind("<B1-Motion>", self.draw)
        self.canvas.bind("<ButtonRelease-1>", self.stop_draw)
        
        self.app.bind("<Control-z>", lambda e: self.undo())
        self.app.bind("<Control-y>", lambda e: self.redo())

    def submit_drawing(self):
        payload = {
            "type": "drawing",
            "round": getattr(self.app.state, "round", 1),
            "author": self.app.state.pseudo,
            "strokes": self.all_strokes
        }
        
        topic = topics.t_submission(
            self.app.state.room_id, 
            payload["round"], 
            self.app.state.pseudo
        )
        
        self.app.net.client.publish_json(topic, payload, qos=1, retain=True)
        print(f"[*] Dessin envoyé sur {topic} ({len(self.all_strokes)} traits)")
        
        self.canvas.unbind("<Button-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
        tk.Label(self, text="Dessin envoyé ! En attente des autres...", 
                 font=("Arial", 14), fg="#F1C40F", bg="#2C3E50").pack(pady=5)

    def set_color(self, color): self.current_color = color
    
    def clear_canvas(self):
        self.canvas.delete("all")
        self.all_strokes = []
        self.redo_stack = []

    def start_draw(self, event):
        self.last_x, self.last_y = event.x, event.y
        self.current_stroke_points = [(event.x, event.y)]

    def draw(self, event):
        if self.last_x and self.last_y:
            self.canvas.create_line(self.last_x, self.last_y, event.x, event.y, 
                                    fill=self.current_color, width=3, capstyle=tk.ROUND)
            self.current_stroke_points.append((event.x, event.y))
            self.last_x, self.last_y = event.x, event.y

    def stop_draw(self, event):
        stroke_data = {"color": self.current_color, "points": self.current_stroke_points}
        self.all_strokes.append(stroke_data)
        self.redo_stack = [] 
        self.last_x, self.last_y = None, None

    def undo(self):
        if self.all_strokes:
            self.redo_stack.append(self.all_strokes.pop())
            self.canvas.delete("all")
            self.redraw_all()

    def redo(self):
        if self.redo_stack:
            self.all_strokes.append(self.redo_stack.pop())
            self.canvas.delete("all")
            self.redraw_all()

    def redraw_all(self):
        for stroke in self.all_strokes:
            color = stroke["color"]
            points = stroke["points"]
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i+1]
                self.canvas.create_line(x1, y1, x2, y2, fill=color, width=3, capstyle=tk.ROUND)
            



class WriteScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="Phase d'Écriture", font=("Arial", 24, "bold"), 
                 fg="white", bg="#2C3E50").pack(pady=40)
        
        tk.Label(self, text="Invente une phrase drôle ou absurde :", 
                 font=("Arial", 16), fg="lightgray", bg="#2C3E50").pack(pady=10)

        self.sentence_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.sentence_entry.pack(pady=20)

        self.submit_btn = tk.Button(self, text="Envoyer la phrase ✔", font=("Arial", 14, "bold"), 
                                    bg="#27AE60", fg="white", command=self.submit_sentence)
        self.submit_btn.pack(pady=20)

        self.status_label = tk.Label(self, text="", font=("Arial", 14), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=10)

    def submit_sentence(self):
        content = self.sentence_entry.get().strip()
        if not content:
            return

        payload = {
            "type": "sentence",
            "round": getattr(self.app.state, "round", 0),
            "author": self.app.state.pseudo,
            "ts": int(time.time()),
            "content": content
        }

        topic = topics.t_submission(self.app.state.room_id, payload["round"], self.app.state.pseudo)

        if self.app.net.client:
            self.app.net.client.publish_json(topic, payload, qos=1, retain=True)
            
        print(f"[*] Phrase envoyée sur {topic} : '{content}'")

        self.sentence_entry.config(state="disabled")
        self.submit_btn.config(state="disabled")
        self.status_label.config(text="Phrase envoyée ! En attente des autres...")

class GuessScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="Que représente ce dessin ?", font=("Arial", 20, "bold"), 
                 fg="white", bg="#2C3E50").pack(pady=10)

        self.canvas = tk.Canvas(self, bg="white", width=600, height=300)
        self.canvas.pack(pady=10)
        
        self.canvas.create_text(300, 150, text="[Le dessin d'un autre joueur s'affichera ici]", 
                                font=("Arial", 14), fill="gray")

        tk.Label(self, text="Ta déduction :", font=("Arial", 14), fg="lightgray", bg="#2C3E50").pack(pady=5)
        
        self.guess_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.guess_entry.pack(pady=10)

        self.submit_btn = tk.Button(self, text="Envoyer la réponse ✔", font=("Arial", 14, "bold"), 
                                    bg="#27AE60", fg="white", command=self.submit_guess)
        self.submit_btn.pack(pady=10)

        self.status_label = tk.Label(self, text="", font=("Arial", 14), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=5)

    def submit_guess(self):
        content = self.guess_entry.get().strip()
        if not content:
            return

        payload = {
            "type": "sentence",
            "round": getattr(self.app.state, "round", 2),
            "author": self.app.state.pseudo,
            "ts": int(time.time()),
            "content": content
        }

        topic = topics.t_submission(self.app.state.room_id, payload["round"], self.app.state.pseudo)

        if self.app.net.client:
            self.app.net.client.publish_json(topic, payload, qos=1, retain=True)
            
        print(f"[*] Déduction envoyée sur {topic} : '{content}'")

        self.guess_entry.config(state="disabled")
        self.submit_btn.config(state="disabled")
        self.status_label.config(text="Réponse envoyée ! En attente des autres...")

class RevealScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")
        tk.Label(self, text="Phase REVEAL : L'album final", font=("Arial", 20), fg="white", bg="#2C3E50").pack(pady=50)