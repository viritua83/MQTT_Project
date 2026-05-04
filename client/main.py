from client.state import ClientState
from client.gui.app import GarticApp

def main():
    state = ClientState()
    app = GarticApp(state)
    app.geometry("1200x800")
    app.mainloop()

if __name__ == "__main__":
    main()