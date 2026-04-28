import tkinter as tk
from .screens import LoginScreen, LobbyScreen, DrawScreen 

class GarticApp(tk.Tk):
    def __init__(self, network_manager, state):
        super().__init__()
        self.net = network_manager
        self.state = state
        self.title("Gartic MQTT")
        
        self.container = tk.Frame(self)
        self.container.pack(fill="both", expand=True)
        
        self.current_screen = None
        
        self.show_screen("LOGIN")

    def show_screen(self, phase):
        if self.current_screen:
            self.current_screen.destroy()
            
        if phase == "LOGIN":
            self.current_screen = LoginScreen(self.container, self)
        elif phase == "LOBBY":
            self.current_screen = LobbyScreen(self.container, self)
        elif phase == "DRAW":
            self.current_screen = DrawScreen(self.container, self)
        
        self.current_screen.pack(fill="both", expand=True)