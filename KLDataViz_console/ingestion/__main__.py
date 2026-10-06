# -*- coding: utf-8 -*-
"""Permet `python -m ingestion …` depuis le dossier de la console KLDataViz."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
