# Données de démonstration — soutenance

Trois fichiers **entièrement fictifs** : aucune donnée réelle, aucune donnée TA-MOH. Ils sont générés par
`generer_donnees_demo.py`, avec une graine fixe pour obtenir les mêmes fichiers à chaque exécution.

```bash
python soutenance/generer_donnees_demo.py
```

| Fichier | Format | Ce qu'il permet de montrer |
|---|---|---|
| `donnees_demo/demo_dhis2_vih_mensuel.xlsx` | Export DHIS2 **long** : Indicateur, Période, Région, District, Valeur (945 lignes, janv.–sept. 2025) | Conversion automatique en une colonne par indicateur. Cascade VIH par région. Indicateurs clés de suivi et écarts « sept. 2025 vs août 2025 ». Choix de la période et comparaison. |
| `donnees_demo/demo_patients_vih.xlsx` | **Une ligne par patient** (1 500 patients, janv. 2024 → 14 nov. 2025) | Taux (initiation le même jour, TPT, CV supprimée) et délai moyen. Pyramide des âges. Seuils cliniques (délai, CD4, charge virale). Écarts mois par mois. **Mois en cours détecté** (novembre). **Imputation détectée** : 22 % des CD4 valent la médiane. |
| `donnees_demo/demo_pivot_districts.xlsx` | Export DHIS2 **pivot** : une ligne par district et par mois, une colonne par indicateur (30 lignes) | Classements par district et écarts entre août et septembre sur des données déjà agrégées. |

## Avant la démo

1. Ouvrir le site **10 minutes avant** : le serveur gratuit se met en veille.
2. Importer les trois fichiers à l'avance et vérifier chaque **Tableau de bord**.
3. Si l'IA ne répond pas, le tableau de bord se construit quand même par règles, avec des valeurs justes mais des titres moins soignés. Il suffit alors de cliquer sur **Régénérer**.
