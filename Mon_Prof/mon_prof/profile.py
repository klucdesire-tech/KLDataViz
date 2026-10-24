# -*- coding: utf-8 -*-
"""Mémoire de l'apprenant : profil, notions (acquises / en cours / à revoir),
historique des séances et journaux. Stockage SQLite local (`Mon_Prof/storage/profil.sqlite`).

Les données de l'élève restent strictement locales (aucun partage hors cadre parent/élève).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "storage" / "profil.sqlite"

STATUTS = ("acquise", "en_cours", "a_revoir")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_schema() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS apprenant (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                prenom TEXT NOT NULL DEFAULT 'Élève',
                classe TEXT,
                programme TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                matiere TEXT NOT NULL,
                notion TEXT NOT NULL,
                statut TEXT NOT NULL CHECK (statut IN ('acquise','en_cours','a_revoir')),
                note TEXT,
                derniere_seance TEXT,
                UNIQUE (matiere, notion)
            );
            CREATE TABLE IF NOT EXISTS seances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_debut TEXT NOT NULL,
                matiere TEXT,
                notion TEXT,
                mode TEXT NOT NULL DEFAULT 'ecrit',
                duree_s INTEGER,
                transcript TEXT,
                statut_final TEXT,
                fiche_synthese TEXT
            );
            """
        )
        c.execute(
            "INSERT OR IGNORE INTO apprenant (id, prenom, created_at) VALUES (1, 'Élève', ?)",
            (_now(),),
        )


def set_eleve(prenom: str, classe: str | None = None, programme: str | None = None) -> None:
    init_schema()
    with _conn() as c:
        c.execute(
            "UPDATE apprenant SET prenom = ?, classe = COALESCE(?, classe), "
            "programme = COALESCE(?, programme) WHERE id = 1",
            (prenom.strip() or "Élève", classe, programme),
        )


def get_eleve() -> dict:
    init_schema()
    with _conn() as c:
        row = c.execute("SELECT prenom, classe, programme FROM apprenant WHERE id = 1").fetchone()
        return {"prenom": row["prenom"], "classe": row["classe"], "programme": row["programme"]}


def start_session(matiere: str, notion: str, mode: str) -> int:
    init_schema()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO seances (date_debut, matiere, notion, mode) VALUES (?, ?, ?, ?)",
            (_now(), matiere, notion, mode),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else 0


def finish_session(session_id: int, transcript: str, statut_final: str | None,
                   fiche_synthese: str | None) -> None:
    with _conn() as c:
        row = c.execute("SELECT date_debut FROM seances WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return
        duree = max(0, int((datetime.now(timezone.utc) -
                            datetime.fromisoformat(row["date_debut"])).total_seconds()))
        c.execute(
            "UPDATE seances SET duree_s = ?, transcript = ?, statut_final = ?, fiche_synthese = ? "
            "WHERE id = ?",
            (duree, transcript, statut_final, fiche_synthese, session_id),
        )


def upsert_notion(matiere: str, notion: str, statut: str, note: str = "") -> None:
    init_schema()
    if statut not in STATUTS:
        statut = "en_cours"
    with _conn() as c:
        c.execute(
            """
            INSERT INTO notions (matiere, notion, statut, note, derniere_seance)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (matiere, notion) DO UPDATE SET
                statut = excluded.statut,
                note = CASE WHEN excluded.note = '' THEN notions.note ELSE excluded.note END,
                derniere_seance = excluded.derniere_seance
            """,
            (matiere, notion, statut, note, _now()),
        )


def bilan() -> dict:
    """Progression par matière et notion : acquises, en cours, à revoir + compteurs."""
    init_schema()
    with _conn() as c:
        notions = [
            {"matiere": r["matiere"], "notion": r["notion"], "statut": r["statut"],
             "derniere_seance": r["derniere_seance"]}
            for r in c.execute("SELECT matiere, notion, statut, derniere_seance "
                               "FROM notions ORDER BY derniere_seance DESC").fetchall()
        ]
        nb_seances = c.execute("SELECT COUNT(*) AS n FROM seances").fetchone()["n"]
    return {
        "nb_seances": nb_seances,
        "acquises": [n for n in notions if n["statut"] == "acquise"],
        "en_cours": [n for n in notions if n["statut"] == "en_cours"],
        "a_revoir": [n for n in notions if n["statut"] == "a_revoir"],
    }
