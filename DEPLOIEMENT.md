# Mettre KLDataViz Studio en ligne

La même base de code sert deux usages :

- **Console locale** (par défaut) : `lancer_console.bat`, sans compte, sur `localhost:8642`.
- **Version en ligne** (`KLDV_MODE=cloud`) : comptes, espaces séparés par client, quotas par plan, IA payée par la plateforme.

## Ce qui est déjà prêt

- **Projet Supabase `kldataviz`** (Francfort, offre gratuite) : `https://xsukewkksvzdncejvtfg.supabase.co`. Tables du schéma `app` créées par `supabase/migrations/`.
- **`render.yaml`** : service web Docker, disque persistant `/data` (5 Go), contrôle de santé `/healthz`.
- **Tests** (dans `KLDataViz_console/`) :
  ```
  .venv\Scripts\python -m pytest tests/test_local.py tests/test_sandbox.py
  .venv\Scripts\python -m pytest tests/test_cloud.py
  ```
  Lance les deux commandes séparément : la seconde démarre le serveur en mode en ligne.

## À faire une seule fois (toi)

### 1. Supabase (tableau de bord du projet `kldataviz`)

1. **Authentication → URL Configuration**
   - *Site URL* : `https://kldataviz.onrender.com` (ou ton domaine).
   - *Redirect URLs* : ajoute `https://kldataviz.onrender.com/**`.
2. **Authentication → Sign In / Providers → Email** : laisse « Confirm email » **activé**. C'est lui qui garantit qu'une invitation ne peut être acceptée que par le vrai propriétaire de l'adresse.
3. **Authentication → Emails → SMTP Settings** : branche Brevo (`smtp-relay.brevo.com`, port 587). L'envoi intégré de Supabase est limité à quelques e-mails par heure.
4. **Project Settings → Database → Connect → Session pooler** : copie la chaîne de connexion (port 5432) et remplace `[YOUR-PASSWORD]` par le mot de passe de la base. Si tu ne le connais pas : *Database → Settings → Reset database password*. Cette chaîne est la valeur de `DATABASE_URL`.

### 2. Brevo (gratuit, 300 e-mails par jour)

1. Crée un compte.
2. Vérifie ton adresse d'expédition (*Senders*).
3. Crée une clé API (*SMTP & API → API Keys*).

### 3. Render

1. Crée un compte sur render.com et relie GitHub.
2. *New → Blueprint* → dépôt `klucdesire-tech/KLDataViz`, branche `feat/cloud` (ou `main` une fois fusionnée).
3. Renseigne les valeurs demandées :

| Variable | Valeur |
|---|---|
| `SUPABASE_URL` | `https://xsukewkksvzdncejvtfg.supabase.co` |
| `SUPABASE_ANON_KEY` | clé « anon » publique (*Project Settings → API*) |
| `DATABASE_URL` | chaîne Session pooler de l'étape 1.4 |
| `DEEPSEEK_API_KEY` | ta clé DeepSeek |
| `BREVO_API_KEY`, `MAIL_FROM` | clé et adresse vérifiée Brevo |
| `ADMIN_EMAILS` | ton adresse e-mail (accès à l'administration des espaces) |
| `APP_URL` | `https://kldataviz.onrender.com` |

4. Lance le déploiement. Coût : environ 7 $/mois (offre Starter) plus 1,25 $/mois pour le disque de 5 Go.

## Après le premier déploiement

1. Ouvre l'URL, crée ton compte (confirmation par e-mail), puis crée ton espace.
2. Dans **Réglages → Administration**, passe ton espace au plan voulu.
3. Fais un parcours complet : import, Vérifier, Analyser, chat, export, planification.
4. Invite un 2ᵉ compte, puis vérifie avec un 3ᵉ compte (autre espace) qu'il ne voit rien.

## Activer le paiement CinetPay

Tant que `CINETPAY_API_KEY` et `CINETPAY_API_PASSWORD` sont vides, l'application affiche « Le paiement en ligne sera bientôt disponible » et rien n'est encaissé.

1. **Avant de déployer le code :** applique `supabase/migrations/20261030000000_paiements.sql`. L'application lit `paid_until` à chaque connexion : sans cette migration, plus personne ne peut se connecter.
2. Dans **Administration → Forfaits et prix**, renseigne le prix mensuel de chaque forfait. Un forfait sans prix reste « sur devis ».
3. Crée le compte marchand CinetPay. Récupère la clé (`sk_test_…`, puis `sk_live_…`) et le mot de passe d'API du compte Côte d'Ivoire.
4. Dans Render, renseigne `CINETPAY_API_KEY`, `CINETPAY_API_PASSWORD` et vérifie `APP_URL` (en `https://`).
5. Demande à CinetPay si l'adresse IP de sortie du serveur doit être déclarée. Render l'indique dans *Connect → Outbound*.
6. Teste avec la clé `sk_test_` : seuls les comptes de `ADMIN_EMAILS` voient alors le paiement (badge « Mode test »). Les clients continuent de voir « bientôt disponible ». Passe ensuite à `sk_live_` et fais un vrai petit paiement.

La liste est dans **Administration → Paiements**. Le bouton **Vérifier** relit un paiement chez CinetPay s'il est resté sans suite. Attribuer un forfait à la main dans l'administration le donne sans échéance ; **Prolonger** ajoute des jours à un abonnement payé.

## Sauvegarde

Deux choses à sauvegarder : la base PostgreSQL (Supabase) et le disque `/data` de Render. `scripts/sauvegarde.py` fait
les deux : base (`pg_dump`) et fichiers (`tar.gz`) dans `/data/sauvegardes/`, rotation des 7 dernières.

- **À la main** : Admin → Santé du service → « Sauvegarder maintenant », ou Render → Shell :
  `python scripts/sauvegarde.py --garder 7`.
- **Copie hors site, chiffrée, une par jour** : une sauvegarde sur le même disque ne protège pas d'une perte du
  disque. Dès que `SAUVEGARDE_S3_BUCKET` est posée, le service fait une sauvegarde chaque nuit (entre 1 h et 6 h UTC)
  et en envoie une copie **chiffrée sur le serveur** vers un stockage compatible S3 (30 copies gardées par sorte).
  Le contrôle « Sauvegarde » de la page Santé (et de `/healthz/complet`) passe en échec si la dernière a échoué ou a
  plus de 30 h.
  1. Cloudflare → R2 → créer un compartiment (ex. `kldataviz-sauvegardes`, région Europe), puis un jeton d'API R2
     « Lecture et écriture d'objets » limité à ce compartiment.
  2. Générer la clé de chiffrement (Render → Shell) : `python scripts/sauvegarde.py --nouvelle-cle`.
     **La recopier aussi dans un gestionnaire de mots de passe** : sans elle, les copies sont illisibles.
  3. Render → Environment : `SAUVEGARDE_S3_BUCKET`, `SAUVEGARDE_S3_ENDPOINT` (`https://<id du compte>.r2.cloudflarestorage.com`),
     `SAUVEGARDE_S3_CLE_ID`, `SAUVEGARDE_S3_CLE_SECRETE`, `SAUVEGARDE_CHIFFREMENT_CLE`.
  Sans clé de chiffrement, rien ne part (aucune copie en clair hors du serveur). Backblaze B2 fonctionne de la même
  façon (son adresse S3 en `SAUVEGARDE_S3_ENDPOINT`).
- **Restauration** : télécharger la copie `.kldv` du compartiment, puis la déchiffrer avec la même clé :
  `SAUVEGARDE_CHIFFREMENT_CLE=… python scripts/sauvegarde.py --dechiffrer base_AAAAMMJJ_HHMMSS.dump.kldv base.dump`.
  Ensuite `pg_restore --clean --no-owner -d "$DATABASE_URL" base.dump` pour la base ;
  `tar xzf donnees_AAAAMMJJ.tar.gz -C /data` pour les fichiers.

## Envoi par WhatsApp (rapports planifiés et alertes)

Inactif tant que les variables ne sont pas posées. Ensuite, un numéro (`+225 07 00 00 00 00`) peut figurer parmi les
destinataires d'un rapport planifié, à côté des e-mails : il reçoit le rapport PDF ou Excel en pièce jointe.

1. **Meta Business** (business.facebook.com) : vérifier l'entreprise, puis WhatsApp Manager → ajouter un numéro
   d'envoi (un numéro qui n'est pas déjà utilisé sur l'application WhatsApp) et un moyen de paiement (Meta facture
   chaque conversation ouverte par un modèle).
2. **Deux modèles**, catégorie « Utilitaire », langue « Français » :
   - rapport (ex. `rapport_kldataviz`) : en-tête **Document** ; corps à 3 variables, par exemple
     « Votre rapport {{1}} du {{2}} est prêt. À retenir : {{3}} » ;
   - alerte (ex. `alerte_kldataviz`) : corps à 1 variable, par exemple « Surveillance KLDataViz : {{1}} ».
   Attendre leur approbation par Meta (quelques minutes à 48 h).
3. **Jeton permanent** : Meta Business → Utilisateurs système → créer un utilisateur « admin », lui donner le compte
   WhatsApp, puis « Générer un jeton » avec `whatsapp_business_messaging` (sans expiration).
4. Render → Environment : `WHATSAPP_JETON`, `WHATSAPP_NUMERO_ID` (WhatsApp Manager → API → « Phone number ID »),
   `WHATSAPP_MODELE_RAPPORT`, `WHATSAPP_MODELE_ALERTE`, et pour les alertes `KLDV_ALERTES_WHATSAPP`
   (numéros d'astreinte séparés par des virgules). Facultatif : `WHATSAPP_LANGUE` (`fr`), `WHATSAPP_INDICATIF`
   (`225`, pour les numéros écrits sans indicatif).
5. Admin → Santé du service → « Envoyer une alerte d'essai » : le message dit combien de numéros WhatsApp l'ont reçue.

Une alerte WhatsApp part même si Brevo est en panne ; elle ne cite que le nombre et le genre des incidents.

## Sécurité : ce qui protège les données des clients

- **Espaces séparés.** Chaque espace a son dossier `/data/tenants/<id>/` et son schéma PostgreSQL. Tout chemin hors de l'espace est refusé.
- **Code de l'IA isolé.** Il est vérifié (AST), puis exécuté dans un processus séparé, sans variables d'environnement, avec une limite de temps et de mémoire (`sandbox.py`).
- **Liens web.** Seuls les liens `https` publics sont acceptés ; les adresses internes et les redirections vers elles sont refusées (`netguard.py`).
- **Liens de téléchargement** signés, valables 2 h, liés à l'espace.
- **Secrets** uniquement dans les variables Render ; rien dans Git ni dans l'image Docker (`.dockerignore`).
- **À ta charge, hors code :** conditions d'utilisation, accord de traitement des données avec tes clients, formalités ARTCI pour les données de santé.
