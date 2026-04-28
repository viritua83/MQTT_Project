from client.state import ClientState
#from client.network import NetworkManager
from client.gui.app import GarticApp

def main():
    state = ClientState()
    
    app = GarticApp(network_manager=None, state=state)
    app.geometry("800x600")
    app.mainloop()

if __name__ == "__main__":
    main()