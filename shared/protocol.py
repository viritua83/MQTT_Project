"""
Constantes du protocole applicatif Gartic Phone.

Centraliser ces chaînes évite les fautes de frappe silencieuses
(par exemple "DRAW" vs "draw") qui sont le cauchemar du debug MQTT.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Phases de la partie (champ "phase" dans l'état)
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    LOBBY = "LOBBY"          # En attente, joueurs rejoignent
    WRITE = "WRITE"          # Round 0 : tout le monde écrit une phrase
    DRAW = "DRAW"            # Round impair : dessiner la phrase reçue
    GUESS = "GUESS"          # Round pair (>=2) : deviner la phrase d'un dessin
    REVEAL = "REVEAL"        # Le serveur fait défiler les albums
    END = "END"              # Partie terminée, retour possible au lobby


# ---------------------------------------------------------------------------
# Types de soumission (champ "type" dans le payload submission)
# ---------------------------------------------------------------------------

class SubmissionType(str, Enum):
    SENTENCE = "sentence"   # Une phrase texte
    DRAWING = "drawing"     # Une liste de traits vectoriels


# ---------------------------------------------------------------------------
# Statuts de présence
# ---------------------------------------------------------------------------

class PresenceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"


# ---------------------------------------------------------------------------
# Paramètres de jeu par défaut
# ---------------------------------------------------------------------------

# Durées par phase, en secondes.
# La phrase est plus rapide que le dessin car elle demande moins de réflexion.
DURATION_WRITE_S = 45
DURATION_DRAW_S = 90
DURATION_GUESS_S = 45
DURATION_REVEAL_STEP_S = 6   # Délai entre chaque étape de la révélation

# Bornes de partie
MIN_PLAYERS = 3
# Pas de max strict — on s'aligne sur la décision du groupe.
# Le nombre de rounds total = nb_joueurs - 1
# (chaque album doit revenir à son auteur en passant par tous les autres).

# Configuration MQTT
BROKER_HOST = "broker.emqx.io"
BROKER_PORT = 1883
KEEPALIVE_S = 10  # Court : permet au broker de détecter un crash en ~15s max


# ---------------------------------------------------------------------------
# Liste de mots pour placeholders serveur (étape 4)
# ---------------------------------------------------------------------------
#
# Utilisée par le serveur UNIQUEMENT comme ceinture de sécurité quand un
# client crash en plein round et n'envoie pas de soumission à temps. En
# conditions normales, le client publie toujours une soumission (même
# vide) à la deadline (cf WriteScreen.update_timer côté client). Donc le
# serveur ne pioche dans cette liste que pour les soumissions
# strictement absentes à la deadline.
#
# Les mots sont volontairement absurdes pour rester dans l'esprit Gartic
# Phone et donner du sens au reveal final.
FALLBACK_WORDS = [
    "un canard motorisé",
    "une banane qui parle",
    "un chat astronaute",
    "une licorne en colère",
    "un robot poète",
    "un dragon qui tricote",
    "une pizza volante",
    "un piano enchanté",
    "un panda ninja",
    "une baleine sur un vélo",
]


# ---------------------------------------------------------------------------
# Helpers de payload
# ---------------------------------------------------------------------------

def round_kind(round_n: int) -> SubmissionType:
    """Détermine si un round attend une phrase ou un dessin.

    Round 0 : phrase de départ
    Round 1 : dessin de la phrase 0
    Round 2 : phrase qui décrit le dessin 1
    Round 3 : dessin de la phrase 2
    ...
    Donc : round pair => SENTENCE, round impair => DRAWING.
    """
    return SubmissionType.SENTENCE if round_n % 2 == 0 else SubmissionType.DRAWING


def phase_for_round(round_n: int) -> Phase:
    """Phase à utiliser pour un round donné."""
    if round_n == 0:
        return Phase.WRITE
    return Phase.DRAW if round_n % 2 == 1 else Phase.GUESS


def duration_for_phase(phase: Phase) -> int:
    """Timer associé à chaque phase."""
    return {
        Phase.WRITE: DURATION_WRITE_S,
        Phase.DRAW: DURATION_DRAW_S,
        Phase.GUESS: DURATION_GUESS_S,
    }.get(phase, 0)