"""
Schémas JSON documentés pour chaque type de payload échangé.

Ce fichier sert de CONTRAT entre le code serveur et le code client.
Toute modification ici doit être annoncée à l'équipe.

Les schémas sont écrits comme des classes (TypedDict) pour bénéficier
de l'autocomplétion IDE, mais ne sont pas validés à l'exécution
(pas de pydantic pour rester léger). C'est de la documentation exécutable.

Convention :
  - `ts` est toujours un timestamp Unix en secondes (entier).
  - `version` est un entier croissant pour le state, permet de détecter
    les messages dans le désordre.
"""

from typing import List, Literal, TypedDict


# ---------------------------------------------------------------------------
# State — publié par le serveur sur t_state(room_id), retained, QoS 1
# ---------------------------------------------------------------------------

class StatePayload(TypedDict):
    phase: Literal["LOBBY", "WRITE", "DRAW", "GUESS", "REVEAL", "END"]
    round: int                    # 0 pour LOBBY/WRITE, 1+ ensuite
    total_rounds: int             # = nombre de joueurs
    deadline_ts: int              # 0 si pas de deadline (LOBBY, REVEAL, END)
    players_order: List[str]      # Ordre figé après START_GAME
    room_id: str
    host: str                     # Pseudo du créateur de la room
    version: int                  # Incrémenté à chaque publish


# ---------------------------------------------------------------------------
# Presence — publié par le client sur t_player_presence(...), retained, QoS 1
# Configuré aussi en LWT pour publier offline si crash.
# ---------------------------------------------------------------------------

class PresencePayload(TypedDict):
    status: Literal["online", "offline"]
    pseudo: str
    ts: int


# ---------------------------------------------------------------------------
# Server presence — publié par le serveur, même format que Presence mais
# pour le serveur lui-même. Sur t_server_presence(room_id).
# ---------------------------------------------------------------------------

# Réutilise PresencePayload (avec pseudo = "__server__" par convention).


# ---------------------------------------------------------------------------
# Ready — publié par le client sur t_player_ready(...), retained, QoS 1
# Permet au serveur de démarrer automatiquement quand tous les joueurs
# en ligne ont cliqué "prêt".
# ---------------------------------------------------------------------------

class ReadyPayload(TypedDict):
    ready: bool
    pseudo: str
    ts: int


# ---------------------------------------------------------------------------
# Submissions — t_submission(room_id, round, pseudo), QoS 1, retained
# Publié par le client, écouté par le serveur.
# ---------------------------------------------------------------------------

class Stroke(TypedDict):
    color: str          # Hexa "#RRGGBB"
    width: int          # Épaisseur en pixels
    points: List[List[int]]   # [[x1,y1], [x2,y2], ...]


class SentenceSubmissionPayload(TypedDict):
    type: Literal["sentence"]
    round: int
    author: str
    ts: int
    content: str        # Le texte saisi


class DrawingSubmissionPayload(TypedDict):
    type: Literal["drawing"]
    round: int
    author: str
    ts: int
    strokes: List[Stroke]
    canvas_size: List[int]   # [width, height] de référence


# ---------------------------------------------------------------------------
# Album entries — t_album(room_id, album_id, round), QoS 1, retained
# Publié par le serveur après consolidation d'un round.
# ---------------------------------------------------------------------------

class AlbumSentenceEntry(TypedDict):
    album_id: str
    round: int
    type: Literal["sentence"]
    content: str
    contributed_by: str       # Qui a produit cette entrée
    original_author: str      # Qui a démarré cet album au round 0


class AlbumDrawingEntry(TypedDict):
    album_id: str
    round: int
    type: Literal["drawing"]
    strokes: List[Stroke]
    canvas_size: List[int]
    contributed_by: str
    original_author: str


# ---------------------------------------------------------------------------
# Reveal pointer — t_reveal_current(room_id), QoS 1, retained
# Publié par le serveur pendant la phase REVEAL.
# ---------------------------------------------------------------------------

class RevealPayload(TypedDict):
    album_id: str       # Album en cours de révélation
    step: int           # Étape actuelle dans cet album (0 = phrase initiale)
    total_steps: int    # = total_rounds
    finished: bool      # True quand tous les albums ont été révélés
