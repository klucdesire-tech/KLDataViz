# -*- coding: utf-8 -*-
"""Copie hors site des sauvegardes : chiffrées sur le serveur, puis envoyées vers un stockage compatible S3
(Cloudflare R2, Backblaze B2…). L'hébergeur du stockage ne reçoit que des octets chiffrés.

Réglages (variables d'environnement, Render) :
- SAUVEGARDE_S3_BUCKET        nom du compartiment ; sans lui, rien n'est envoyé ;
- SAUVEGARDE_S3_ENDPOINT      adresse du service S3 (R2 : https://<compte>.r2.cloudflarestorage.com) ;
- SAUVEGARDE_S3_CLE_ID / SAUVEGARDE_S3_CLE_SECRETE   clé d'accès au compartiment ;
- SAUVEGARDE_CHIFFREMENT_CLE  clé de chiffrement, 32 octets en base64 (`python scripts/sauvegarde.py --nouvelle-cle`).
  Sans elle, aucun envoi : jamais de sauvegarde en clair hors du serveur. La perdre = ne plus pouvoir relire les copies.

Format d'un fichier chiffré (« .kldv ») : AES-256-GCM par blocs de 4 Mo, pour chiffrer de gros fichiers sans les
charger en mémoire. En-tête = « KLDVSAV1 » + 8 octets aléatoires ; chaque bloc = sa longueur sur 4 octets + le bloc
chiffré. Le numéro du bloc et la marque « dernier bloc » entrent dans les données authentifiées : un bloc modifié,
déplacé, ou un fichier tronqué est refusé au déchiffrement."""
import base64
import binascii
import os
import struct

MAGIC = b"KLDVSAV1"
BLOC = 4 * 1024 * 1024
PREFIXE = "kldataviz/"
GARDER_DISTANT = 30  # copies distantes conservées par sorte (base, fichiers)


class Erreur(Exception):
    """Réglage manquant ou fichier chiffré illisible : le message est lisible par un administrateur."""


def nouvelle_cle() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def lire_cle(texte) -> bytes:
    try:
        cle = base64.b64decode(str(texte or "").strip(), validate=True)
    except (binascii.Error, ValueError):
        raise Erreur("SAUVEGARDE_CHIFFREMENT_CLE n'est pas du base64 valide.")
    if len(cle) != 32:
        raise Erreur("SAUVEGARDE_CHIFFREMENT_CLE doit faire 32 octets (générez-la avec --nouvelle-cle).")
    return cle


def config():
    """Réglages de l'envoi, ou None si aucun compartiment n'est configuré. Erreur si la configuration est incomplète."""
    env = os.environ.get
    bucket = (env("SAUVEGARDE_S3_BUCKET") or "").strip()
    if not bucket:
        return None
    manquants = [n for n in ("SAUVEGARDE_S3_ENDPOINT", "SAUVEGARDE_S3_CLE_ID", "SAUVEGARDE_S3_CLE_SECRETE",
                             "SAUVEGARDE_CHIFFREMENT_CLE") if not (env(n) or "").strip()]
    if manquants:
        raise Erreur("Copie hors site impossible, réglages manquants : " + ", ".join(manquants) + ".")
    return {"bucket": bucket, "endpoint": env("SAUVEGARDE_S3_ENDPOINT").strip(), "cle_id": env("SAUVEGARDE_S3_CLE_ID").strip(),
            "cle_secrete": env("SAUVEGARDE_S3_CLE_SECRETE").strip(), "cle": lire_cle(env("SAUVEGARDE_CHIFFREMENT_CLE"))}


def _aad(prefixe, n, dernier):
    return MAGIC + prefixe + struct.pack(">I?", n, dernier)


def chiffrer(source, cible, cle):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes, prefixe = AESGCM(cle), os.urandom(8)
    with open(source, "rb") as src, open(cible, "wb") as dst:
        dst.write(MAGIC + prefixe)
        n, bloc = 0, src.read(BLOC)
        while True:
            suivant = src.read(BLOC) if bloc else b""
            dernier = not suivant
            nonce = prefixe + struct.pack(">I", n)
            chiffre = aes.encrypt(nonce, bloc, _aad(prefixe, n, dernier))
            dst.write(struct.pack(">I", len(chiffre)) + chiffre)
            if dernier:
                return
            n, bloc = n + 1, suivant


def dechiffrer(source, cible, cle):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aes = AESGCM(cle)
    with open(source, "rb") as src, open(cible, "wb") as dst:
        tete = src.read(len(MAGIC) + 8)
        if len(tete) != len(MAGIC) + 8 or not tete.startswith(MAGIC):
            raise Erreur("Ce fichier n'est pas une sauvegarde chiffrée par KLDataViz.")
        prefixe, n = tete[len(MAGIC):], 0
        longueur = src.read(4)
        while longueur:
            if len(longueur) != 4:
                raise Erreur("Sauvegarde tronquée.")
            chiffre = src.read(struct.unpack(">I", longueur)[0])
            longueur = src.read(4)
            try:
                dst.write(aes.decrypt(prefixe + struct.pack(">I", n), chiffre, _aad(prefixe, n, not longueur)))
            except InvalidTag:
                raise Erreur("Déchiffrement refusé : mauvaise clé, ou fichier modifié ou tronqué.")
            if not longueur:
                return
            n += 1
        raise Erreur("Sauvegarde vide ou tronquée.")


def _client(cfg):
    import boto3
    return boto3.client("s3", endpoint_url=cfg["endpoint"], aws_access_key_id=cfg["cle_id"],
                        aws_secret_access_key=cfg["cle_secrete"], region_name="auto")


def envoyer(chemins, cfg, garder=GARDER_DISTANT, client=None, journal=print):
    """Chiffre puis envoie chaque fichier ; garde les `garder` copies distantes les plus récentes de chaque sorte.
    Renvoie les clés des objets envoyés. Le fichier chiffré temporaire est toujours effacé."""
    client = client or _client(cfg)
    faits = []
    for chemin in chemins:
        temporaire = chemin + ".kldv"
        try:
            chiffrer(chemin, temporaire, cfg["cle"])
            cle_objet = PREFIXE + os.path.basename(temporaire)
            client.upload_file(temporaire, cfg["bucket"], cle_objet)
            faits.append(cle_objet)
            journal(f"[sauvegarde] copie chiffrée envoyée : {cle_objet}")
        finally:
            if os.path.exists(temporaire):
                os.remove(temporaire)
    _rotation(client, cfg["bucket"], garder, journal)
    return faits


def _rotation(client, bucket, garder, journal):
    noms = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIXE):
        noms += [o["Key"] for o in page.get("Contents", [])]
    for sorte in ("base_", "donnees_"):
        anciens = sorted((k for k in noms if os.path.basename(k).startswith(sorte)), reverse=True)[garder:]
        for k in anciens:  # le nom porte la date AAAAMMJJ_HHMMSS : l'ordre alphabétique est l'ordre du temps
            client.delete_object(Bucket=bucket, Key=k)
            journal(f"[sauvegarde] ancienne copie distante supprimée : {k}")
