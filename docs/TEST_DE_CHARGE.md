# Test de charge de KLDataViz

Combien d'utilisateurs simultanés le serveur tient-il ? Le script `KLDataViz_console/scripts/test_charge.py` le mesure :
des utilisateurs simulés ouvrent des pages et/ou lancent des calculs lourds (analyse + tableau de bord), et le script
compte les réussites, les refus (429, 503), les erreurs et les temps de réponse. Avec `--pid`, il mesure aussi le
processeur et la mémoire du serveur, puis estime ce que tiendrait la machine Render « Starter » (0,5 processeur, 512 Mo).

## Lancer un test en local (recommandé)

Serveur local sans IA (pas de `llm_config.json`, pas d'`ANTHROPIC_API_KEY`), données dans un dossier jetable, limite
de 30 calculs par minute levée (tous les utilisateurs simulés partagent l'espace « local ») :

```bash
cd KLDataViz_console
KLDV_DATA_DIR=/tmp/kldv_charge KLDV_LIMITE_CALCULS=100000 KLDAVIZ_PORT=8650 python server.py
```

Dans un autre terminal (le numéro de processus du serveur sert à `--pid`) :

```bash
python scripts/test_charge.py --url http://127.0.0.1:8650 --scenario pages --utilisateurs 300 --duree 60 --pid <pid>
python scripts/test_charge.py --url http://127.0.0.1:8650 --scenario lourd --utilisateurs 6 --duree 90 --lignes 20000 --pid <pid>
python scripts/test_charge.py --url http://127.0.0.1:8650 --scenario mixte --utilisateurs 100 --duree 120 --lignes 5000 --pid <pid>
```

`--lignes` fabrique un fichier **fictif** à partir du jeu d'exemple (aucune donnée réelle). `--json fichier.json`
garde le résultat.

## Sur le site en ligne

À éviter tant que des utilisateurs réels s'en servent. Si besoin : heure creuse, compte de test, en commençant petit.
`--oui-production` est obligatoire ; les calculs lourds demandent le jeton de session du compte de test dans la variable
d'environnement `KLDV_JETON` (jamais en argument) et consomment son quota.

## Résultats du 13/11/2026

Mesuré sur un PC Windows (pas sur Render), sans IA, avec des fichiers fictifs. Les chiffres « Starter » sont des
**plafonds théoriques** (processeur seul) : la vraie machine fera au mieux autant. La mémoire maximale est relevée
toutes les 0,25 s (borne basse) ; le script donne aussi le pic exact depuis le lancement du serveur.

| Essai | Résultat | Processeur par action | Starter, au mieux |
|---|---|---|---|
| 300 visiteurs, pages seules (une page toutes les 2 s chacun) | 7 912 requêtes en 63 s, **0 échec**, médiane 16 à 36 ms | 7 ms par requête (page, script, logo ou contrôle) | ~70 requêtes/s, soit ~140 visiteurs à ce rythme |
| 4 utilisateurs qui calculent, exemple (216 lignes) | 33 calculs, 0 échec, médiane 4 à 5 s | 1,3 s par calcul | ~22 calculs par minute |
| 6 utilisateurs qui calculent, 20 000 lignes | 55 calculs, 0 échec, médiane 8 à 11 s, mémoire max 291 Mo | 2,2 s | ~14 calculs par minute |
| 2 utilisateurs qui calculent, 100 000 lignes (9 Mo) | 8 calculs, 0 échec, médiane 19 à 23 s, mémoire max 335 Mo | 11,7 s | ~2,6 calculs par minute |
| 100 utilisateurs mélangés (80 visiteurs, 20 qui calculent, 5 000 lignes) | pages ralenties (95 % sous 0,75 s au lieu de 0,06 s, 9 connexions refusées) ; **6 calculs réussis sur 40** en 2 min, 16 « serveur occupé », 18 délais dépassés | | |

## Ce que le test a révélé

1. **Les tableaux de bord échappent à la file des calculs lourds.** `analyze` et le code de l'IA passent par `_lourd`
   (2 à la fois au plus), mais pas `build_dashboard` : 15 tableaux de bord calculaient en même temps pendant l'essai
   mélangé (relevé py-spy). Chacun ralentit tous les autres ; sur Starter, environ 100 Mo par tableau de 100 000 lignes
   en cours, donc 4 suffisent à dépasser 512 Mo et à faire redémarrer le service pour tout le monde. L'absence de garde
   est volontaire (`_build_dashboard` : l'appel à l'IA, jusqu'à 2 minutes, bloquerait un des 2 jetons) : la correction
   doit protéger les calculs sans retenir le jeton pendant l'attente de l'IA.
2. **Un calcul abandonné continue.** Quand le navigateur abandonne, le serveur continue de calculer : plusieurs minutes
   à 100 % de processeur sans personne au bout.
3. **5 connexions en attente seulement.** `socketserver` garde par défaut 5 connexions en attente
   (`request_queue_size`) ; quand les calculs occupent le processeur, les suivantes sont refusées.
4. **Les pages ne sont pas le problème** : 300 visiteurs simultanés sans un échec tant que personne ne calcule.

En clair : 1 000 personnes qui ouvrent les pages, une machine plus grosse suffit. 1 000 personnes qui génèrent un
tableau de bord en même temps : aujourd'hui, sans file, la mémoire déborde et le service redémarre ; une fois le
point 1 corrigé, c'est 45 à 70 minutes de file sur Starter, donc la plupart verraient « serveur occupé ».
Corriger le point 1 d'abord (petit changement), puis agrandir la machine et augmenter `KLDV_MAX_HEAVY` à proportion.

## Après correction (14/11/2026)

Correction : `build_dashboard` passe par la file (`@_lourd`) et rend son jeton le temps de l'appel à l'IA
(`_hors_jeton`) ; « occupé » reste une réponse 503 ; le lien public prend le même jeton sans attendre
(`_jeton_lourd(attente=0)`) ; 64 connexions en attente au lieu de 5. Même essai, même PC, avant et après :
100 utilisateurs mélangés (80 visiteurs, 20 qui calculent), fichier fictif de 20 000 lignes, 2 minutes.

| | Avant (e6de4bc) | Après |
|---|---|---|
| Calculs réussis (analyse + tableau de bord) | 19 sur 49 | **24 sur 40** |
| Refus « serveur occupé » (503, message clair) | 17 | 16 |
| Délais dépassés ou connexions coupées sur les calculs | 13 | **0** |
| Connexions refusées sur les pages | 354 sur 3 620 (10 %) | **0 sur 3 692** |
| Pages : médiane / 95 % | 0,12 à 0,22 s / 1,7 à 2,2 s | 0,08 à 0,16 s / 2,6 à 3,0 s |
| Mémoire maximale du serveur | 370 Mo | **307 Mo** |
| Tableaux de bord calculés en même temps | jusqu'à 15 | 2 au plus |

Les pages servies le sont un peu plus lentement au 95e centile parce qu'aucune n'est plus refusée : celles qui
échouaient avant attendent maintenant leur tour. Le point 2 (calcul abandonné qui continue) n'est pas traité ici.

5. **Nouveau constat : le contrôle des valeurs hors habitude est lent sur un export de type DHIS2.** Sur le fichier
   fictif de 5 000 lignes (environ 280 établissements × 18 mois × 8 indicateurs), une seule analyse consomme ~44 s de
   processeur, dont 97 % dans `qualite._extremes_temps` : `_serie_extremes` est appelée série par série (≈ 2 200 fois,
   une dizaine d'opérations pandas chacune). C'est ce qui expliquait les résultats de l'essai mélangé du 13/11 sur
   5 000 lignes. À vectoriser dans une PR séparée, avec un test qui vérifie les mêmes constats qu'avant.
