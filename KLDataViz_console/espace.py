# -*- coding: utf-8 -*-
"""Données d'un espace : export complet (.zip) et suppression définitive, réservés au propriétaire.

Pour des données de santé, l'organisation doit pouvoir récupérer tout ce qu'elle a déposé et en obtenir
l'effacement. L'export reprend fichiers importés, copies nettoyées, tableaux de bord enregistrés, rapports,
dictionnaire et journal ; il exclut les fichiers techniques temporaires et le coffre chiffré DHIS2 (mots de passe).
La suppression efface ces mêmes dossiers, révoque les liens de partage et vide le registre d'ingestion de l'espace.
"""
import os
import shutil
import time
import zipfile

import tenancy as T

DOSSIERS = ("uploads", "exports", "storage")  # les copies nettoyées sont dans storage/cleaned
EXCLUS = ("tmp", "dhis2")  # temporaires ; coffre chiffré (identifiants)


def _fichiers(root):
    for d in DOSSIERS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, files in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUS]
            for fn in files:
                p = os.path.join(dirpath, fn)
                if not p.endswith((".tmp",)) and "journal_admin" not in fn:
                    yield p


def exporter() -> str:
    """Crée l'archive de l'espace dans ses exports et renvoie son nom de fichier."""
    ctx = T.current()
    root = ctx.root
    nom = time.strftime("export_espace_%Y%m%d_%H%M%S.zip")
    dest = os.path.join(T.exports_dir(), nom)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _fichiers(root):
            if os.path.abspath(p) == os.path.abspath(dest) or os.path.basename(p).startswith("export_espace_"):
                continue
            z.write(p, os.path.relpath(p, root))
        z.writestr("LISEZMOI.txt", "Export KLDataViz de l'espace « " + str((ctx.plan or {}).get("org_name") or "local")
                   + " » du " + time.strftime("%d/%m/%Y %H:%M") + ".\nuploads : fichiers importés ; cleaned : copies "
                   "corrigées ; storage : tableaux de bord, dictionnaire, journal ; exports : rapports.\n")
    return nom


def supprimer_donnees(revoke_all=None) -> dict:
    """Efface toutes les données de l'espace (irréversible). `revoke_all(id)` révoque les liens de partage."""
    if not T.CLOUD:  # en local, l'« espace » est le dossier de la console : on n'efface jamais l'ordinateur de l'utilisateur
        raise ValueError("Suppression disponible dans la version en ligne. En local, vos fichiers restent sur cet ordinateur.")
    ctx = T.current()
    root = ctx.root
    if not T.current().owns(os.path.join(root, "x")):
        raise PermissionError("espace inconnu")
    try:
        import sources
        ids = [s.get("id") for s in sources.list_sources()]
    except Exception:
        ids = []
    for i in ids:
        if revoke_all and i:
            try:
                revoke_all(i)
            except Exception:
                pass
    n = 0
    for d in DOSSIERS + ("tmp",):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            for _ in _compte(p):
                n += 1
            shutil.rmtree(p, ignore_errors=True)
    registre = False
    if T.CLOUD:
        try:
            import cloud
            cloud.q(f'drop schema if exists "{ctx.schema}" cascade')  # nom dérivé d'un identifiant vérifié (uuid)
            registre = True
        except Exception:
            registre = False
    return {"fichiers_supprimes": n, "liens_revoques": len(ids), "registre_vide": registre}


def _compte(p):
    for _d, _s, files in os.walk(p):
        yield from files
