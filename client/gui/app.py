import tkinter as tk
from client.network import NetworkManager
from .screens import MenuScreen, LobbyScreen, DrawScreen, WriteScreen, GuessScreen, RevealScreen 

DEBUG_MODE = True

class GarticApp(tk.Tk):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.net = NetworkManager(self)
        self.title("Gartic MQTT")
        
        if DEBUG_MODE:
            self.debug_bar = tk.Frame(self, bg="black", pady=5)
            self.debug_bar.pack(side="bottom", fill="x")
            tk.Label(self.debug_bar, text="⚙️ DEBUG :", fg="yellow", bg="black", font=("Arial", 10, "bold")).pack(side="left", padx=10)
            phases_to_test = ["MENU", "LOBBY", "WRITE", "DRAW", "GUESS", "REVEAL"]
            for phase in phases_to_test:
                tk.Button(self.debug_bar, text=phase, bg="#555", fg="white", command=lambda p=phase: self.show_screen(p)).pack(side="left", padx=5)

        self.container = tk.Frame(self)
        self.container.pack(side="top", fill="both", expand=True)
        
        self.current_screen = None
        self.net.connect_menu()
        self.show_screen("MENU")

    def show_screen(self, phase):
        if self.current_screen:
            self.current_screen.destroy()
            
        if phase == "MENU":
            self.current_screen = MenuScreen(self.container, self)
        elif phase == "LOBBY":
            self.current_screen = LobbyScreen(self.container, self)
        elif phase == "WRITE":
            self.current_screen = WriteScreen(self.container, self)
        elif phase == "DRAW":
            self.current_screen = DrawScreen(self.container, self)
        elif phase == "GUESS":
            self.current_screen = GuessScreen(self.container, self)
        elif phase == "REVEAL":
            self.current_screen = RevealScreen(self.container, self)
            
        self.current_screen.pack(fill="both", expand=True)