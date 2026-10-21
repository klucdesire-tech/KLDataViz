# MON PROF — Professeur particulier virtuel

Agent d'éducation scolaire à domicile : il accompagne un élève ou un étudiant, à l'écrit ou à
l'oral, jusqu'à ce qu'il comprenne vraiment la notion étudiée.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `SPEC.md` | Spécification de référence : rôle, mission, objectifs, outils, contraintes, permissions, critères de réussite |
| `PROMPT.md` | Prompt système prêt à intégrer (comportement pédagogique + déroulé d'une séance) |
| `mon_prof/` | Agent exécutable (package Python stdlib, sans dépendance) — dont `voice.py` (TTS neuronal edge-tts + STT Whisper) |
| `llm_config.example.json` | Modèle de configuration LLM (à copier en `llm_config.json`) |

## Lancement

Package **stdlib uniquement** (`urllib`, `sqlite3`, `json`) : il tourne avec n'importe quel
Python 3.10+, sans `pip install`.

```bash
cd Mon_Prof
python -m mon_prof                                          # séance interactive
python -m mon_prof --matiere maths --notion "étude de fonction"
python -m mon_prof bilan                                    # progression de l'apprenant
python -m mon_prof profil                                   # profil de l'apprenant
python -m mon_prof eleve --prenom Aya --classe "Terminale D" --programme ivoirien
```

Pendant une séance : `/fin` (clôturer + fiche de synthèse + enregistrer la notion),
`/bilan` (progression), `/quit` (quitter en enregistrant « en cours »), `/parle`
(répondre à la voix, si la voix est activée).

### Voix naturelle (Windows)

**Synthèse (TTS)** — deux moteurs, choisis automatiquement :
1. **edge-tts** — voix neuronales naturelles (Microsoft), gratuites, sans clé.
   Voix par défaut : `fr-FR-DeniseNeural` (femme). Modifiable via `MONPROF_VOICE` :
   `fr-FR-HenriNeural` (homme), `fr-FR-EloiseNeural` (enfant).
2. **SAPI 5** (voix intégrées Windows) — repli automatique si `edge-tts` n'est pas installé.

**Reconnaissance (STT)** — deux moteurs, choisis automatiquement :
1. **Whisper** (`faster-whisper` + `sounddevice`) — reconnaissance locale, précise,
   excellent en français. Modèle par défaut : `base` (modifiable via `MONPROF_WHISPER_MODEL`).
2. **SAPI 5** — repli automatique si Whisper n'est pas installé.

Installation (une seule fois) :
```bash
uv venv .venv
uv pip install edge-tts sounddevice faster-whisper
```

Utilisation :
```bash
.venv/Scripts/python.exe -m mon_prof --voix                       # lit les réponses à voix haute
.venv/Scripts/python.exe -m mon_prof --mode oral --matiere anglais --notion "prononciation"
```

`--voix` lit chaque réponse à voix haute ; `/parle` écoute ta réponse au micro
(Whisper : ~2 s de chargement la 1re fois, modèle téléchargé une seule fois).
Hors Windows, la voix est neutralisée automatiquement.

## Configuration LLM (DeepSeek)

La clé est résolue dans l'ordre :
1. variable d'environnement `DEEPSEEK_API_KEY` (ou `MONPROF_API_KEY`) ;
2. `Mon_Prof/llm_config.json` (copier depuis l'exemple) ;
3. `KLDataViz_console/llm_config.json` (clé déjà utilisée par la console).

Aucune clé n'est stockée dans le dépôt (`llm_config.json` et `storage/` sont ignorés par Git).

## Démarrage d'une séance

1. L'apprenant donne sa **classe/filière** et la **matière** (ex. « Terminale D — maths : étude de fonction »).
2. L'agent évalue le niveau de départ.
3. Il explique, fait pratiquer, corrige, puis vérifie la compréhension réelle.

## Exemples de demandes

- « Terminale C — maths : les nombres complexes »
- « 3ᵉ — français : le commentaire de texte »
- « Licence 2 Droit — droit des obligations »
- « Seconde — anglais : le présent perfect »

## Règles clés (rappel)

- Ne jamais faire le devoir à la place de l'élève : guider par des questions, donner la solution seulement après plusieurs tentatives, en l'expliquant.
- Ne jamais accepter un « j'ai compris » sans question de contrôle ou exercice de validation.
- Exact sur le fond : en cas de doute, dire « je vérifie » plutôt que d'inventer.
- Protéger les données de l'élève (stockage local, jamais partagé hors du cadre parent/élève).
