"""
Logique de rotation des albums entre joueurs.

Principe Gartic Phone :
  - Chaque joueur initie un album avec sa phrase au round 0.
  - À chaque round, l'album passe au joueur suivant dans l'ordre.
  - Au round R, l'album passe R fois donc se trouve chez le joueur
    décalé de R positions par rapport à son auteur.

On nomme les albums "A0", "A1", ... "A<n-1>" où Ak est l'album dont
l'auteur initial est players_order[k].

La fonction critique : étant donné (pseudo, round), savoir quel album
ce joueur est en train de travailler. Calculée localement par chaque
client, ce qui évite au serveur de l'envoyer explicitement.
"""

from typing import List


def album_id_for_index(index: int) -> str:
    """Convention de nommage des albums."""
    return f"A{index}"


def author_index_of_album(album_id: str) -> int:
    """Inverse de album_id_for_index."""
    assert album_id.startswith("A"), f"album_id invalide : {album_id}"
    return int(album_id[1:])


def album_assigned_to_player(
    pseudo: str,
    round_n: int,
    players_order: List[str],
) -> str:
    """Retourne l'album sur lequel `pseudo` doit travailler au round donné.

    Au round 0 chacun travaille sur son propre album.
    Au round R l'album reçu est celui de R positions en arrière.

    Exemple avec players_order = [alice, bob, charlie] :
      round 0 : alice -> A0, bob -> A1, charlie -> A2  (chacun écrit le sien)
      round 1 : alice -> A2, bob -> A0, charlie -> A1  (décalage -1)
      round 2 : alice -> A1, bob -> A2, charlie -> A0  (décalage -2)
    """
    if pseudo not in players_order:
        raise ValueError(f"Joueur {pseudo} pas dans players_order")
    n = len(players_order)
    my_index = players_order.index(pseudo)
    # On remonte la chaîne : l'album reçu est celui de quelqu'un situé
    # `round_n` positions en arrière dans l'ordre.
    author_index = (my_index - round_n) % n
    return album_id_for_index(author_index)


def previous_round_album_content_topic_round(round_n: int) -> int:
    """Le contenu à afficher au joueur au round R est celui qu'a produit
    le joueur précédent au round R-1.

    Cette fonction existe surtout pour rendre explicite le décalage
    (sinon on a souvent du off-by-one).
    """
    return round_n - 1


def total_rounds_for_players(n_players: int) -> int:
    """Nombre total de rounds pour que chaque album fasse un tour complet.

    Avec N joueurs, l'album doit passer par N-1 autres mains après le round 0.
    Donc rounds 0..N-1 inclus, soit N rounds au total.
    """
    return n_players
