# Gartic Phone — MQTT

Projet ISEN 2026 — Implémentation d'un Gartic Phone distribué via MQTT.

## Stack

- **Langage** : Python 3.10+
- **Broker MQTT** : `broker.emqx.io:1883`
- **Lib MQTT** : paho-mqtt
- **GUI** : Tkinter (builtin)

## Architecture

```
gartic-mqtt/
├── shared/         # Code partagé entre serveur et client
│   ├── topics.py       # Définition des topics MQTT (source de vérité)
│   ├── protocol.py     # Phases, durées, constantes
│   ├── rotation.py     # Logique de rotation des albums
│   ├── schemas.py      # Schémas JSON documentés (contrat)
│   └── mqtt_client.py  # Wrapper paho avec reconnexion auto + LWT
│
├── server/         # Serveur arbitre (Dev A)
├── client/         # Client GUI Tkinter (Dev B)
│
└── tests/manual/
    ├── fake_client.py  # Faux client console pour tester le serveur
    └── fake_server.py  # Faux serveur pour tester le client
```

## Installation

```bash
pip install -r requirements.txt
```

## Lancement (à venir une fois server/ et client/ codés)

```bash
# Terminal 1 : serveur arbitre
python -m server.main <room_id>

# Terminal 2..N : clients joueurs
python -m client.main <pseudo> <room_id>
```

## Test sans GUI (côté serveur)

```bash
# Lancer 3 fake_clients pour simuler une partie complète
python -m tests.manual.fake_client alice room1
python -m tests.manual.fake_client bob   room1
python -m tests.manual.fake_client charlie room1
```

## Test sans serveur (côté client)

```bash
# Faire défiler les phases pour tester chaque écran de la GUI
python -m tests.manual.fake_server room1 --scenario draw
```

## Topics MQTT

Tout est préfixé par `isen-2026-gN/gartic/`. Voir `shared/topics.py` pour les détails.

| Topic | QoS | Retained | LWT |
|-------|-----|----------|-----|
| `rooms-index/<roomId>` | 0 | ✅ | — |
| `rooms/<roomId>/state` | 1 | ✅ | — |
| `rooms/<roomId>/presence/<pseudo>` | 1 | ✅ | ✅ |
| `rooms/<roomId>/server/presence` | 1 | ✅ | ✅ |
| `rooms/<roomId>/ready/<pseudo>` | 1 | ✅ | — |
| `rooms/<roomId>/submissions/round/<n>/<pseudo>` | 1 | ✅ | — |
| `rooms/<roomId>/albums/<albumId>/round/<n>` | 1 | ✅ | — |
| `rooms/<roomId>/reveal/current` | 1 | ✅ | — |
