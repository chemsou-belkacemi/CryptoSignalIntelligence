"""`python -m crypto_signal_intelligence.api [--host 127.0.0.1] [--port 8503]`"""
from __future__ import annotations

import argparse
import logging

from ..config import load_settings
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="API locale de CSI : lecture et évaluation seulement.")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute (défaut : 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8503, help="port (défaut : 8503)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    serve(load_settings(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
