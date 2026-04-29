"""
Point d'entrée du serveur arbitre Gartic Phone.

Usage :
    python -m server.main

Rôle minimal :
  - Configurer le logging.
  - Instancier ServerApp.
  - Capturer SIGINT et SIGTERM pour appeler ServerApp.stop().
  - Bloquer sur ServerApp.run() jusqu'à l'arrêt.

Toute la logique métier est dans server/server_app.py.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from server.server_app import ServerApp


def _configure_logging(level: str) -> None:
    """Format compact mais lisible, optimisé pour la démo en direct."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Serveur arbitre Gartic-MQTT")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de log (défaut: INFO)",
    )
    args = parser.parse_args()
    _configure_logging(args.log_level)

    app = ServerApp()

    # Handler unifié pour SIGINT (Ctrl+C) et SIGTERM (kill).
    # L'idée : appeler stop() proprement, ce qui publie offline avant
    # de couper la connexion. Si on coupait sans ça, les clients
    # devraient attendre la LWT (~15s) pour voir qu'on est parti.
    def _on_signal(signum, _frame):
        signame = signal.Signals(signum).name
        logging.info("Signal %s reçu", signame)
        app.stop()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        app.run()
    except Exception:
        logging.exception("Crash inattendu du serveur")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
