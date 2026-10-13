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

## Sécurité : ce qui protège les données des clients

- **Espaces séparés.** Chaque espace a son dossier `/data/tenants/<id>/` et son schéma PostgreSQL. Tout chemin hors de l'espace est refusé.
- **Code de l'IA isolé.** Il est vérifié (AST), puis exécuté dans un processus séparé, sans variables d'environnement, avec une limite de temps et de mémoire (`sandbox.py`).
- **Liens web.** Seuls les liens `https` publics sont acceptés ; les adresses internes et les redirections vers elles sont refusées (`netguard.py`).
- **Liens de téléchargement** signés, valables 2 h, liés à l'espace.
- **Secrets** uniquement dans les variables Render ; rien dans Git ni dans l'image Docker (`.dockerignore`).
- **À ta charge, hors code :** conditions d'utilisation, accord de traitement des données avec tes clients, formalités ARTCI pour les données de santé.
