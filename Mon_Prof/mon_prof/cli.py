# -*- coding: utf-8 -*-
"""Interface en ligne de commande de Mon Prof."""
from __future__ import annotations

import argparse

from . import agent, profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mon_prof",
        description="Mon Prof — professeur particulier virtuel à domicile.",
    )
    sub = parser.add_subparsers(dest="command")

    # Sans sous-commande : démarre une séance.
    parser.add_argument("--matiere", default=None)
    parser.add_argument("--notion", default=None)
    parser.add_argument("--mode", default="ecrit", choices=("ecrit", "oral"))
    parser.add_argument("--model", default=None, help="ex. deepseek-flash ou deepseek-v4-pro")
    parser.add_argument("--voix", action="store_true",
                        help="lire les réponses à voix haute (+ /parle pour répondre à la voix)")

    sub.add_parser("bilan", help="progression de l'apprenant")
    sub.add_parser("profil", help="afficher le profil de l'apprenant")

    p_eleve = sub.add_parser("eleve", help="renseigner le profil de l'apprenant")
    p_eleve.add_argument("--prenom", required=True)
    p_eleve.add_argument("--classe", default=None)
    p_eleve.add_argument("--programme", default=None)

    args = parser.parse_args(argv)

    if args.command == "bilan":
        return agent.show_bilan()
    if args.command == "profil":
        return agent.show_profil()
    if args.command == "eleve":
        profile.set_eleve(args.prenom, args.classe, args.programme)
        print(f"Profil enregistré : {args.prenom} ({args.classe or '—'}, "
              f"{args.programme or '—'}).")
        return 0
    # commande par défaut : séance
    return agent.run_session(matiere=args.matiere, notion=args.notion, mode=args.mode,
                             model=args.model, voix=args.voix or args.mode == "oral")


if __name__ == "__main__":
    raise SystemExit(main())
