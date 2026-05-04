import tkinter as tk
from tkinter import simpledialog
import secrets
import time
from shared import topics

class MenuScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="Gartic MQTT", font=("Arial", 28, "bold"), fg="white", bg="#2C3E50").pack(pady=20)

        self.server_status_label = tk.Label(self, text="🔴 Serveur hors ligne", font=("Arial", 14, "bold"), fg="#E74C3C", bg="#2C3E50")
        self.server_status_label.pack(pady=10)

        tk.Label(self, text="Ton Pseudo :", font=("Arial", 14), fg="white", bg="#2C3E50").pack(pady=5)
        self.pseudo_entry = tk.Entry(self, font=("Arial", 14), justify="center")
        self.pseudo_entry.pack(pady=5)

        self.rooms_frame = tk.LabelFrame(self, text="Salons disponibles", bg="#34495E", fg="white", font=("Arial", 12), padx=10, pady=10)
        self.rooms_frame.pack(pady=20, fill="both", expand=True, padx=50)

        self.rooms_list_inner = tk.Frame(self.rooms_frame, bg="#34495E")
        self.rooms_list_inner.pack(fill="both", expand=True)

        self.create_btn = tk.Button(self, text="Créer une room", font=("Arial", 14, "bold"), bg="#2980B9", fg="white", command=self.create_room_popup)
        self.create_btn.pack(pady=10)

        self.status_label = tk.Label(self, text="", font=("Arial", 12, "italic"), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=5)

        self.pending_room_id = None

        self.update_server_status()
        self.update_rooms_list()

    def update_server_status(self):
        if self.app.state.server_online:
            self.server_status_label.config(text="🟢 Serveur en ligne", fg="#2ECC71")
        else:
            self.server_status_label.config(text="🔴 Serveur hors ligne", fg="#E74C3C")

    def update_rooms_list(self):
        for widget in self.rooms_list_inner.winfo_children():
            widget.destroy()

        if not self.app.state.available_rooms:
            tk.Label(self.rooms_list_inner, text="Aucune salle disponible.", font=("Arial", 12, "italic"), fg="lightgray", bg="#34495E").pack(pady=20)
        else:
            for room in self.app.state.available_rooms:
                room_id = room.get("id")
                name = room.get("name")
                n_players = room.get("n_players")
                phase = room.get("phase")

                btn_text = f"{name} ({n_players} joueur(s)) - [{phase}]"
                state = tk.NORMAL if phase == "LOBBY" else tk.DISABLED
                bg_color = "#27AE60" if phase == "LOBBY" else "#7F8C8D"

                tk.Button(self.rooms_list_inner, text=btn_text, font=("Arial", 12, "bold"), bg=bg_color, fg="white", state=state, 
                          command=lambda r=room_id: self.join_specific_room(r)).pack(fill="x", pady=5, padx=20)

        if self.pending_room_id:
            if any(r.get("id") == self.pending_room_id for r in self.app.state.available_rooms):
                room_id_to_join = self.pending_room_id
                self.pending_room_id = None 
                pseudo = self.pseudo_entry.get().strip()
                self.app.net.enter_room(room_id_to_join, pseudo)
                self.app.show_screen("LOBBY")

    def create_room_popup(self):
        pseudo = self.pseudo_entry.get().strip()
        if not pseudo:
            self.status_label.config(text="⚠️ Choisis un pseudo d'abord !", fg="#E74C3C")
            return

        room_name = simpledialog.askstring("Nouvelle Salle", "Nom de la salle :", parent=self)
        if room_name:
            self.pending_room_id = secrets.token_hex(3)
            payload = {"room_id": self.pending_room_id, "name": room_name}
            
            self.create_btn.config(state="disabled")
            self.status_label.config(text=f"⏳ Création de '{room_name}' en cours...", fg="#F1C40F")
            
            self.app.net.client.publish_json(topics.t_rooms_create(), payload, qos=0)
            
            self.app.after(3000, self.check_creation_timeout)

    def check_creation_timeout(self):
        if not self.winfo_exists():
            return
            
        if self.pending_room_id:
            self.status_label.config(text="❌ Échec de la création (Le serveur n'a pas répondu).", fg="#E74C3C")
            self.pending_room_id = None
            self.create_btn.config(state="normal")

    def join_specific_room(self, room_id):
        pseudo = self.pseudo_entry.get().strip()
        if not pseudo:
            self.status_label.config(text="⚠️ Choisis un pseudo d'abord !", fg="#E74C3C")
            return
            
        self.app.net.enter_room(room_id, pseudo)
        self.app.show_screen("LOBBY")

class LobbyScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        room_name = "Inconnu"
        for r in self.app.state.available_rooms:
            if r.get("id") == self.app.state.room_id:
                room_name = r.get("name")
                break

        tk.Label(self, text=f"Lobby - {room_name}", font=("Arial", 24, "bold"), fg="white", bg="#2C3E50").pack(pady=10)
        tk.Label(self, text=f"ID secret : {self.app.state.room_id}", font=("Arial", 12, "italic"), fg="#F1C40F", bg="#2C3E50").pack(pady=0)
        tk.Label(self, text=f"Connecté : {self.app.state.pseudo}", font=("Arial", 12), fg="lightgray", bg="#2C3E50").pack(pady=5)

        self.ready_count_label = tk.Label(self, text="", font=("Arial", 14, "bold"), fg="#2ECC71", bg="#2C3E50")
        self.ready_count_label.pack(pady=10)
        
        self.min_players_warning = tk.Label(self, text="", font=("Arial", 10, "italic"), fg="#E74C3C", bg="#2C3E50")
        self.min_players_warning.pack(pady=0)

        self.players_label = tk.Label(self, text="", font=("Arial", 14), fg="#3498DB", bg="#2C3E50")
        self.players_label.pack(pady=10)

        self.ready_btn = tk.Button(self, text="Je suis prêt !", font=("Arial", 14, "bold"), bg="#E67E22", fg="white", command=self.toggle_ready)
        self.ready_btn.pack(pady=10)

        tk.Button(self, text="Quitter la salle", font=("Arial", 12), bg="#C0392B", fg="white", command=self.leave_room).pack(pady=20)
        
        self.update_players_list()

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

    def leave_room(self):
        topic_presence = topics.t_player_presence(self.app.state.room_id, self.app.state.pseudo)
        self.app.net.client.publish_json(topic_presence, {"status": "offline", "pseudo": self.app.state.pseudo, "ts": int(time.time())}, retain=True)
        
        topic_ready = topics.t_player_ready(self.app.state.room_id, self.app.state.pseudo)
        self.app.net.client.publish_json(topic_ready, {"ready": False, "pseudo": self.app.state.pseudo, "ts": int(time.time())}, retain=True)

        self.app.state.is_ready = False
        self.app.state.players = []
        self.app.state.ready_players = []
        
        self.after(300, self._execute_leave)

    def _execute_leave(self):
        self.app.net.connect_menu()
        self.app.show_screen("MENU")

    def update_players_list(self):
        text = "Joueur(s) en ligne :\n"
        for p in self.app.state.players:
            status = "✅" if p in self.app.state.ready_players else "⏳"
            text += f"- {p} {status}\n"
        self.players_label.config(text=text)

        total = len(self.app.state.players)
        ready = len([p for p in self.app.state.players if p in self.app.state.ready_players])
        
        if total < 3:
            self.ready_count_label.config(text=f"{ready} / 3 joueurs prêts", fg="#F1C40F")
            self.min_players_warning.config(text="(Minimum 3 joueurs requis pour démarrer)")
        else:
            self.ready_count_label.config(text=f"{ready} / {total} joueurs prêts", fg="#2ECC71")
            self.min_players_warning.config(text="")


class DrawScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="À toi de dessiner !", font=("Arial", 18, "bold"), fg="white", bg="#2C3E50").pack(pady=10)

        self.toolbar = tk.Frame(self, bg="gray")
        self.toolbar.pack(side="top", fill="x", padx=20)

        self.current_color = "black"
        self.current_width = 3
        self.colors = ["black", "red", "green", "blue", "yellow", "orange", "purple", "white"]
        
        tk.Button(self.toolbar, text="Envoyer le dessin ✔", bg="#27AE60", fg="white", font=("Arial", 10, "bold"), command=self.submit_drawing).pack(side="right", padx=10)
        tk.Button(self.toolbar, text="Effacer", command=self.clear_canvas).pack(side="right", padx=5)
        tk.Button(self.toolbar, text="Redo ↪", command=self.redo).pack(side="right", padx=2)
        tk.Button(self.toolbar, text="Undo ↩", command=self.undo).pack(side="right", padx=2)

        for color in self.colors:
            btn = tk.Button(self.toolbar, bg=color, width=3, command=lambda c=color: self.set_color(c))
            btn.pack(side="left", padx=2, pady=2)

        tk.Label(self.toolbar, text="  Épaisseur:", bg="gray", fg="white").pack(side="left")
        tk.Button(self.toolbar, text="Fin", command=lambda: self.set_width(2)).pack(side="left", padx=2)
        tk.Button(self.toolbar, text="Moyen", command=lambda: self.set_width(5)).pack(side="left", padx=2)
        tk.Button(self.toolbar, text="Épais", command=lambda: self.set_width(10)).pack(side="left", padx=2)

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
            "strokes": self.all_strokes,
            "canvas_size": [600, 400],
            "ts": int(time.time())
        }
        
        topic = topics.t_submission(self.app.state.room_id, payload["round"], self.app.state.pseudo)
        
        self.app.net.client.publish_json(topic, payload, qos=1, retain=True)
        
        self.canvas.unbind("<Button-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
        tk.Label(self, text="Dessin envoyé ! En attente des autres...", font=("Arial", 14), fg="#F1C40F", bg="#2C3E50").pack(pady=5)

    def set_color(self, color): 
        self.current_color = color

    def set_width(self, width):
        self.current_width = width
    
    def clear_canvas(self):
        self.canvas.delete("all")
        self.all_strokes = []
        self.redo_stack = []

    def start_draw(self, event):
        self.last_x, self.last_y = event.x, event.y
        self.current_stroke_points = [(event.x, event.y)]

    def draw(self, event):
        if self.last_x and self.last_y:
            self.canvas.create_line(self.last_x, self.last_y, event.x, event.y, fill=self.current_color, width=self.current_width, capstyle=tk.ROUND)
            self.current_stroke_points.append((event.x, event.y))
            self.last_x, self.last_y = event.x, event.y

    def stop_draw(self, event):
        stroke_data = {
            "color": self.current_color,
            "width": self.current_width,
            "points": self.current_stroke_points
        }
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
            width = stroke.get("width", 3)
            for i in range(len(points) - 1):
                x1, y1 = points[i]
                x2, y2 = points[i+1]
                self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width, capstyle=tk.ROUND)

class WriteScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        round_n = getattr(self.app.state, "round", 0)
        tk.Label(self, text=f"Phase d'Écriture - Round {round_n}", font=("Arial", 24, "bold"), fg="white", bg="#2C3E50").pack(pady=20)
        
        self.timer_label = tk.Label(self, text="Temps restant : --", font=("Arial", 20, "bold"), fg="#E74C3C", bg="#2C3E50")
        self.timer_label.pack(pady=10)

        tk.Label(self, text="Invente une phrase drôle ou absurde :", font=("Arial", 16), fg="lightgray", bg="#2C3E50").pack(pady=10)

        self.sentence_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.sentence_entry.pack(pady=20)

        self.submit_btn = tk.Button(self, text="Envoyer la phrase ✔", font=("Arial", 14, "bold"), bg="#27AE60", fg="white", command=self.submit_sentence)
        self.submit_btn.pack(pady=20)

        self.status_label = tk.Label(self, text="", font=("Arial", 14), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=10)

        self.submitted = False
        self.update_timer()

    def update_timer(self):
        if not self.winfo_exists():
            return
            
        rem = self.app.state.deadline_ts - int(time.time())
        if rem <= 0:
            self.timer_label.config(text="Temps écoulé !")
            if not self.submitted:
                self.submit_sentence()
        else:
            self.timer_label.config(text=f"Temps restant : {rem}s")
            self.after(1000, self.update_timer)

    def submit_sentence(self):
        if self.submitted:
            return
            
        self.submitted = True
        content = self.sentence_entry.get().strip()
        if not content:
            content = "[Pas d'inspiration]"

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

        self.sentence_entry.config(state="disabled")
        self.submit_btn.config(state="disabled")
        self.status_label.config(text="Phrase envoyée ! En attente des autres...")

class GuessScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text="Que représente ce dessin ?", font=("Arial", 20, "bold"), fg="white", bg="#2C3E50").pack(pady=10)

        self.canvas = tk.Canvas(self, bg="white", width=600, height=300)
        self.canvas.pack(pady=10)
        
        self.canvas.create_text(300, 150, text="[Le dessin d'un autre joueur s'affichera ici]", font=("Arial", 14), fill="gray")

        tk.Label(self, text="Ta déduction :", font=("Arial", 14), fg="lightgray", bg="#2C3E50").pack(pady=5)
        
        self.guess_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.guess_entry.pack(pady=10)

        self.submit_btn = tk.Button(self, text="Envoyer la réponse ✔", font=("Arial", 14, "bold"), bg="#27AE60", fg="white", command=self.submit_guess)
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

        self.guess_entry.config(state="disabled")
        self.submit_btn.config(state="disabled")
        self.status_label.config(text="Réponse envoyée ! En attente des autres...")

class RevealScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")
        tk.Label(self, text="Phase REVEAL : L'album final", font=("Arial", 20), fg="white", bg="#2C3E50").pack(pady=50)