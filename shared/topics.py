"""
Source unique de vérité pour les topics MQTT du projet Gartic Phone.

Aucun topic en dur ailleurs dans le code : on importe d'ici.
Tous les topics sont préfixés par PREFIX pour éviter les collisions
sur le broker public emqx.

Convention :
  - Les fonctions `t_xxx()` construisent un topic concret pour publier.
  - Les fonctions `sub_xxx()` retournent un topic (potentiellement avec
    wildcard) à utiliser pour subscribe.
"""

# Préfixe namespacé exigé par le sujet du projet.
# À adapter avec le numéro de groupe quand il sera fixé.
PREFIX = "isen-2026-VTGC/gartic"


# ---------------------------------------------------------------------------
# Topics globaux (hors room)
# ---------------------------------------------------------------------------

def t_rooms_index() -> str:
    """Index global des rooms existantes. Retained, QoS 1.

    Payload JSON unique listant toutes les rooms avec leurs métadonnées
    (id, nom affiché, nb joueurs, phase). Maintenu par le serveur :
    chaque création/suppression/changement de phase republie l'index complet.
    """
    return f"{PREFIX}/rooms-index"


def t_rooms_create() -> str:
    """Demande de création de room. NON retained, QoS 0.

    Publié par un client qui veut créer une room. Le serveur écoute et
    crée la room (sauf collision d'id, ignorée silencieusement).
    QoS 0 car best-effort : si le message se perd, le client le verra
    via son timeout (room non apparue dans l'index) et pourra retenter.
    """
    return f"{PREFIX}/rooms-create"


def t_server_presence() -> str:
    """Présence du serveur arbitre, GLOBALE. Retained, QoS 1, LWT.

    Un seul serveur gère toutes les rooms, donc une seule LWT serveur.
    Si le serveur crash, les clients de toutes les rooms le voient via
    ce topic unique.
    """
    return f"{PREFIX}/server/presence"

# ---------------------------------------------------------------------------
# Topics propres à une room
# ---------------------------------------------------------------------------

def t_state(room_id: str) -> str:
    """État global de la partie. Retained, QoS 1.

    Publié par le serveur à chaque transition de phase.
    Lu en premier par tout client qui (re)connecte.
    """
    return f"{PREFIX}/rooms/{room_id}/state"


def t_player_presence(room_id: str, pseudo: str) -> str:
    """Présence d'un joueur. Retained, QoS 1, LWT.

    Publié par le client à la connexion (online) et configuré comme LWT
    (offline) auprès du broker pour détecter les crashes.
    """
    return f"{PREFIX}/rooms/{room_id}/presence/{pseudo}"


def sub_all_player_presence(room_id: str) -> str:
    """Wildcard pour observer la présence de tous les joueurs de la room."""
    return f"{PREFIX}/rooms/{room_id}/presence/+"


def t_player_ready(room_id: str, pseudo: str) -> str:
    """État "prêt" d'un joueur. Retained, QoS 1.

    Publié par le client quand il clique le bouton "prêt" / "pas prêt".
    Le serveur compte les joueurs prêts pour démarrer automatiquement
    la partie quand tous les joueurs en ligne sont prêts.

    Retained pour qu'à la reconnexion un joueur voie immédiatement
    qui est déjà prêt sans attendre une re-publication.
    """
    return f"{PREFIX}/rooms/{room_id}/ready/{pseudo}"


def sub_all_player_ready(room_id: str) -> str:
    """Wildcard pour observer le statut prêt de tous les joueurs."""
    return f"{PREFIX}/rooms/{room_id}/ready/+"


def t_submission(room_id: str, round_n: int, pseudo: str) -> str:
    """Soumission brute d'un joueur pour un round. QoS 1, retained.

    Retained pour deux raisons :
      1. Si le joueur crash après envoi, à la reconnexion il revoit
         ce qu'il a déjà soumis (utile pour l'UX).
      2. Le serveur peut connaître les soumissions sans dépendre de
         l'ordre d'arrivée des messages.
    """
    return f"{PREFIX}/rooms/{room_id}/submissions/round/{round_n}/{pseudo}"


def sub_all_submissions_round(room_id: str, round_n: int) -> str:
    """Wildcard côté serveur pour collecter les soumissions d'un round."""
    return f"{PREFIX}/rooms/{room_id}/submissions/round/{round_n}/+"


def sub_all_submissions(room_id: str) -> str:
    """Wildcard pour collecter toutes les soumissions de tous les rounds."""
    return f"{PREFIX}/rooms/{room_id}/submissions/round/+/+"


def t_album(room_id: str, album_id: str, round_n: int) -> str:
    """Vue consolidée d'un album à un round donné. QoS 1, retained.

    Publié par le serveur après avoir collecté toutes les soumissions
    d'un round et appliqué la rotation. C'est ce que les clients lisent
    pour savoir quelle phrase dessiner ou quel dessin deviner.
    """
    return f"{PREFIX}/rooms/{room_id}/albums/{album_id}/round/{round_n}"


def sub_all_albums(room_id: str) -> str:
    """Wildcard pour suivre tous les albums (utile en phase reveal et reconnexion)."""
    return f"{PREFIX}/rooms/{room_id}/albums/+/+"


def t_reveal_current(room_id: str) -> str:
    """Pointeur sur ce qui est en train d'être révélé. QoS 1, retained.

    Pendant la phase REVEAL, le serveur incrémente ça pour faire défiler
    les albums. Un joueur qui rejoint en cours de reveal voit immédiatement
    où en est la révélation.
    """
    return f"{PREFIX}/rooms/{room_id}/reveal/current"
