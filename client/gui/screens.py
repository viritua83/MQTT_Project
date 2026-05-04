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

        tk.Button(self, text="Créer une room", font=("Arial", 14, "bold"), bg="#2980B9", fg="white", command=self.create_room_popup).pack(pady=20)

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
            return

        for room in self.app.state.available_rooms:
            room_id = room.get("id")
            name = room.get("name")
            n_players = room.get("n_players")
            phase = room.get("phase")

            btn_text = f"{name} ({n_players} joueurs) - [{phase}]"
            
            state = tk.NORMAL if phase == "LOBBY" else tk.DISABLED
            bg_color = "#27AE60" if phase == "LOBBY" else "#7F8C8D"

            tk.Button(self.rooms_list_inner, text=btn_text, font=("Arial", 12, "bold"), bg=bg_color, fg="white", state=state, 
                      command=lambda r=room_id: self.join_specific_room(r)).pack(fill="x", pady=5, padx=20)

    def create_room_popup(self):
        pseudo = self.pseudo_entry.get().strip()
        if not pseudo:
            return

        room_name = simpledialog.askstring("Nouvelle Salle", "Nom de la salle :", parent=self)
        if room_name:
            room_id = secrets.token_hex(3)
            payload = {"room_id": room_id, "name": room_name}
            
            self.app.net.client.publish_json(topics.t_rooms_create(), payload, qos=0)
            self.app.net.enter_room(room_id, pseudo)
            self.app.show_screen("LOBBY")

    def join_specific_room(self, room_id):
        pseudo = self.pseudo_entry.get().strip()
        if not pseudo:
            return
            
        self.app.net.enter_room(room_id, pseudo)
        self.app.show_screen("LOBBY")

        
class LobbyScreen(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(self, text=f"Lobby - Salle : {self.app.state.room_id}", font=("Arial", 20, "bold"), fg="white", bg="#2C3E50").pack(pady=10)
        tk.Label(self, text="(Donne cet ID pour qu'on te rejoigne)", font=("Arial", 10, "italic"), fg="#F1C40F", bg="#2C3E50").pack(pady=0)
        tk.Label(self, text=f"Connecté : {self.app.state.pseudo}", font=("Arial", 12), fg="lightgray", bg="#2C3E50").pack(pady=5)

        self.players_label = tk.Label(self, text="Joueurs en ligne :\n- " + self.app.state.pseudo, font=("Arial", 14), fg="#3498DB", bg="#2C3E50")
        self.players_label.pack(pady=20)

        self.ready_btn = tk.Button(self, text="Je suis prêt !", font=("Arial", 14, "bold"), bg="#E67E22", fg="white", command=self.toggle_ready)
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
        text = "Joueurs en ligne :\n"
        for p in self.app.state.players:
            text += f"- {p}\n"
        self.players_label.config(text=text)

class DrawScreen(tk.Frame):
    """Écran de dessin (rounds impairs).

    Étape 5 :
      - Calcule l'album assigné à ce joueur pour le round courant via
        la rotation (cf shared/rotation.py).
      - Lit l'album à l'index `round-1` (la phrase écrite par le joueur
        précédent dans la chaîne) et l'affiche en haut du canvas.
      - Affiche "Chargement..." si l'album n'est pas encore arrivé
        (race condition possible quand on bascule juste après le
        publish des albums par le serveur). Réessaie à la réception
        d'un album via on_album_received().
      - Compte à rebours basé sur state.deadline_ts.
      - Auto-soumission à la deadline (avec ce que le joueur a dessiné,
        même rien : strokes vides → le serveur prendra ça tel quel).
      - Désactive le canvas après soumission.
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        # En-tête : titre + timer + phrase à dessiner.
        tk.Label(
            self, text=f"À toi de dessiner ! - Round {self.app.state.round}",
            font=("Arial", 18, "bold"), fg="white", bg="#2C3E50",
        ).pack(pady=5)

        self.timer_label = tk.Label(
            self, text="Temps restant : --",
            font=("Arial", 14, "bold"), fg="#E74C3C", bg="#2C3E50",
        )
        self.timer_label.pack(pady=2)

        # Étape 5 : zone d'affichage de la phrase à dessiner.
        # Le label est mis à jour par self._refresh_prompt().
        self.prompt_label = tk.Label(
            self, text="Chargement de la phrase...",
            font=("Arial", 16, "italic"), fg="#F1C40F", bg="#2C3E50",
            wraplength=600, justify="center",
        )
        self.prompt_label.pack(pady=8)

        # Indicateur "joueur parti" affiché si contributor_left=True.
        self.left_warning = tk.Label(
            self, text="", font=("Arial", 11, "italic"),
            fg="#E67E22", bg="#2C3E50",
        )
        self.left_warning.pack(pady=0)

        # Toolbar de dessin (identique à avant).
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

        # Étape 5 : suivi de la soumission + timer.
        self.submitted = False
        self.status_label = tk.Label(self, text="", font=("Arial", 13, "italic"), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=2)

        # Affichage initial du prompt + démarrage du timer.
        self._refresh_prompt()
        self._update_timer()

    def on_album_received(self):
        """Notifié par network._on_album quand un album arrive.

        Étape 5 : utilisé pour rafraîchir l'affichage si on était en
        attente de l'album à dessiner (race condition à la transition).
        """
        if self.winfo_exists():
            self._refresh_prompt()

    def _refresh_prompt(self):
        """Met à jour le label avec la phrase à dessiner.

        Lit dans state.albums l'album assigné à ce joueur, à l'index
        round-1 (la phrase produite par le joueur précédent dans la
        chaîne). Si l'album n'est pas encore arrivé, affiche un message
        d'attente — on_album_received() relancera _refresh_prompt
        à l'arrivée du retained.
        """
        try:
            from shared.rotation import album_assigned_to_player
        except ImportError:
            self.prompt_label.config(text="Erreur : module rotation introuvable")
            return

        round_n = self.app.state.round
        players_order = self.app.state.players_order
        pseudo = self.app.state.pseudo

        # Sécurité : au tout début ou si players_order pas encore reçu.
        if not players_order or pseudo not in players_order:
            self.prompt_label.config(text="Chargement de la partie...")
            return

        album_id = album_assigned_to_player(pseudo, round_n, players_order)
        prev_round = round_n - 1

        # Lit l'album. Si pas encore là, attend (on_album_received
        # relancera la méthode).
        album_for_round = self.app.state.albums.get(album_id, {})
        entry = album_for_round.get(prev_round)
        if entry is None:
            self.prompt_label.config(
                text=f"⏳ Chargement de l'album {album_id}...",
                fg="#F39C12",
            )
            return

        content = entry.get("content", "(phrase manquante)")
        self.prompt_label.config(
            text=f"« {content} »",
            fg="#F1C40F",
        )

        # Indicateur "le joueur précédent a quitté" si le placeholder
        # serveur l'a marqué.
        if entry.get("contributor_left"):
            contributor = entry.get("contributed_by", "?")
            self.left_warning.config(
                text=f"⚠️ {contributor} a quitté la partie : phrase générée automatiquement",
            )
        else:
            self.left_warning.config(text="")

    def _update_timer(self):
        """Compte à rebours basé sur state.deadline_ts.

        À 0, déclenche l'auto-soumission. Boucle via after(1000).
        """
        if not self.winfo_exists():
            return
        rem = self.app.state.deadline_ts - int(time.time())
        if rem <= 0:
            self.timer_label.config(text="Temps écoulé !")
            if not self.submitted:
                self.submit_drawing()
        else:
            self.timer_label.config(text=f"Temps restant : {rem}s")
            self.after(1000, self._update_timer)

    def submit_drawing(self):
        """Publie la soumission dessin sur le bon topic.

        Idempotent via self.submitted : le bouton et l'auto-soumission
        peuvent appeler la méthode plusieurs fois sans risque.
        """
        if self.submitted:
            return
        self.submitted = True

        payload = {
            "type": "drawing",
            "round": self.app.state.round,
            "author": self.app.state.pseudo,
            "strokes": self.all_strokes,
            "canvas_size": [600, 400],
            "ts": int(time.time()),
        }

        topic = topics.t_submission(self.app.state.room_id, payload["round"], self.app.state.pseudo)

        if self.app.net.client:
            self.app.net.client.publish_json(topic, payload, qos=1, retain=True)

        # Désactive les bindings du canvas pour empêcher les modifs
        # post-soumission (UX : évite que le joueur continue à dessiner
        # alors que sa soumission est partie).
        self.canvas.unbind("<Button-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
        self.status_label.config(text="Dessin envoyé ! En attente des autres...")

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
            "points": self.current_stroke_points,
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

        tk.Label(self, text="Phase d'Écriture", font=("Arial", 24, "bold"), fg="white", bg="#2C3E50").pack(pady=40)
        tk.Label(self, text="Invente une phrase drôle ou absurde :", font=("Arial", 16), fg="lightgray", bg="#2C3E50").pack(pady=10)

        self.sentence_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.sentence_entry.pack(pady=20)

        self.submit_btn = tk.Button(self, text="Envoyer la phrase ✔", font=("Arial", 14, "bold"), bg="#27AE60", fg="white", command=self.submit_sentence)
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
            
        self.sentence_entry.config(state="disabled")
        self.submit_btn.config(state="disabled")
        self.status_label.config(text="Phrase envoyée ! En attente des autres...")

class GuessScreen(tk.Frame):
    """Écran de devinette (rounds pairs >= 2).

    Étape 5 :
      - Calcule l'album assigné à ce joueur via la rotation.
      - Lit l'album à l'index `round-1` (le dessin produit par le joueur
        précédent dans la chaîne) et le redessine sur un canvas en
        lecture seule via canvas.create_line.
      - Affiche "Chargement..." si l'album n'est pas encore arrivé,
        on_album_received() relance le rendu.
      - Champ texte pour la devinette + bouton envoyer.
      - Compte à rebours basé sur deadline_ts.
      - Auto-soumission à la deadline (avec fallback texte si vide).
    """

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.configure(bg="#2C3E50")

        tk.Label(
            self, text=f"Que représente ce dessin ? - Round {self.app.state.round}",
            font=("Arial", 18, "bold"), fg="white", bg="#2C3E50",
        ).pack(pady=5)

        self.timer_label = tk.Label(
            self, text="Temps restant : --",
            font=("Arial", 14, "bold"), fg="#E74C3C", bg="#2C3E50",
        )
        self.timer_label.pack(pady=2)

        # Canvas en lecture seule pour afficher le dessin reçu.
        self.canvas = tk.Canvas(self, bg="white", width=600, height=400)
        self.canvas.pack(pady=10)

        # Texte affiché tant que le dessin n'est pas arrivé. On le retire
        # via canvas.delete("placeholder") une fois rendu.
        self.canvas.create_text(
            300, 200, text="⏳ Chargement du dessin...",
            font=("Arial", 14), fill="gray", tags="placeholder",
        )

        # Indicateur "joueur parti" pour le contributeur précédent.
        self.left_warning = tk.Label(
            self, text="", font=("Arial", 11, "italic"),
            fg="#E67E22", bg="#2C3E50",
        )
        self.left_warning.pack(pady=0)

        tk.Label(
            self, text="Ta déduction :",
            font=("Arial", 14), fg="lightgray", bg="#2C3E50",
        ).pack(pady=5)

        self.guess_entry = tk.Entry(self, font=("Arial", 16), width=40, justify="center")
        self.guess_entry.pack(pady=5)

        self.submit_btn = tk.Button(
            self, text="Envoyer la réponse ✔",
            font=("Arial", 14, "bold"), bg="#27AE60", fg="white",
            command=self.submit_guess,
        )
        self.submit_btn.pack(pady=10)

        self.status_label = tk.Label(self, text="", font=("Arial", 13, "italic"), fg="#F1C40F", bg="#2C3E50")
        self.status_label.pack(pady=2)

        self.submitted = False
        self.drawing_rendered = False

        # Tente d'afficher le dessin (s'il est déjà arrivé) puis démarre
        # le timer.
        self._refresh_drawing()
        self._update_timer()

    def on_album_received(self):
        """Notifié par network._on_album quand un album arrive.

        Étape 5 : si on attendait le dessin du round précédent, on le
        rend maintenant.
        """
        if self.winfo_exists() and not self.drawing_rendered:
            self._refresh_drawing()

    def _refresh_drawing(self):
        """Affiche le dessin du round précédent.

        Lit l'album assigné à ce joueur, à l'index round-1. Si pas
        encore arrivé, garde le placeholder et attend on_album_received.
        Si arrivé, redessine les strokes sur le canvas en lecture seule.
        """
        try:
            from shared.rotation import album_assigned_to_player
        except ImportError:
            return

        round_n = self.app.state.round
        players_order = self.app.state.players_order
        pseudo = self.app.state.pseudo

        print(f"[DBG _refresh_prompt] pseudo={pseudo} round={round_n} order={players_order}")
        print(f"[DBG _refresh_prompt] state.albums={self.app.state.albums}")

        if not players_order or pseudo not in players_order:
            return

        album_id = album_assigned_to_player(pseudo, round_n, players_order)
        prev_round = round_n - 1

        print(f"[DBG _refresh_prompt] album_id calculé={album_id} prev_round={prev_round}")
        print(f"[DBG _refresh_prompt] state.albums.get({album_id})={self.app.state.albums.get(album_id)}")
   

        album_for_round = self.app.state.albums.get(album_id, {})
        entry = album_for_round.get(prev_round)
        if entry is None:
            # Pas encore arrivé, on attend on_album_received.
            return

        # On efface le placeholder "Chargement..." et on dessine.
        self.canvas.delete("placeholder")

        strokes = entry.get("strokes", [])
        canvas_size = entry.get("canvas_size", [600, 400])
        # Mise à l'échelle : le dessin a pu être fait à une taille
        # différente du canvas d'affichage. On scale linéairement.
        # Ici la canvas_size source devrait être [600, 400] (cf
        # DrawScreen.submit_drawing) et notre canvas affichage est
        # aussi 600x400 → scale de 1.0. On garde le calcul pour
        # robustesse au cas où on changerait les tailles plus tard.
        src_w, src_h = canvas_size if len(canvas_size) >= 2 else (600, 400)
        dst_w, dst_h = 600, 400
        sx = dst_w / src_w if src_w else 1.0
        sy = dst_h / src_h if src_h else 1.0

        for stroke in strokes:
            color = stroke.get("color", "black")
            width = max(1, int(stroke.get("width", 3)))
            points = stroke.get("points", [])
            for i in range(len(points) - 1):
                p1, p2 = points[i], points[i+1]
                # Les points peuvent être [x, y] (liste) ou (x, y) (tuple).
                # On gère les deux formes.
                x1, y1 = p1[0] * sx, p1[1] * sy
                x2, y2 = p2[0] * sx, p2[1] * sy
                self.canvas.create_line(
                    x1, y1, x2, y2,
                    fill=color, width=width, capstyle=tk.ROUND,
                )

        # Cas particulier : dessin vide (placeholder serveur ou joueur
        # qui n'a rien dessiné). On affiche un message dédié pour que
        # le joueur ne croie pas à un bug.
        if not strokes:
            self.canvas.create_text(
                300, 200, text="(dessin vide)",
                font=("Arial", 12, "italic"), fill="lightgray",
            )

        # Indicateur "joueur parti".
        if entry.get("contributor_left"):
            contributor = entry.get("contributed_by", "?")
            self.left_warning.config(
                text=f"⚠️ {contributor} a quitté la partie : dessin généré automatiquement",
            )
        else:
            self.left_warning.config(text="")

        self.drawing_rendered = True

    def _update_timer(self):
        if not self.winfo_exists():
            return
        rem = self.app.state.deadline_ts - int(time.time())
        if rem <= 0:
            self.timer_label.config(text="Temps écoulé !")
            if not self.submitted:
                self.submit_guess(auto=True)
        else:
            self.timer_label.config(text=f"Temps restant : {rem}s")
            self.after(1000, self._update_timer)

    def submit_guess(self, auto=False):
        """Publie la devinette sur le bon topic.

        Idempotent via self.submitted. En auto-submit (deadline), on
        accepte un champ vide et on met un placeholder texte (le
        serveur préfère un payload qu'une absence : ça évite que le
        serveur génère son propre fallback).
        """
        if self.submitted:
            return

        content = self.guess_entry.get().strip()
        if not content:
            if auto:
                content = "[Pas d'idée]"
            else:
                # Clic manuel sur "Envoyer" sans rien : on ignore (UX).
                return

        self.submitted = True

        payload = {
            "type": "sentence",
            "round": self.app.state.round,
            "author": self.app.state.pseudo,
            "ts": int(time.time()),
            "content": content,
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