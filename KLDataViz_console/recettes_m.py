# -*- coding: utf-8 -*-
"""Export d'une recette de transformation en Power Query (langage M), pour Excel et Power BI.

Chaque étape de la recette devient une étape M, dans le même ordre, avec un nom lisible : la remise en forme faite
dans KLDataViz se refait à l'identique dans Excel (Données → Obtenir des données → Requête vide → Éditeur avancé) ou
dans Power BI. La source (fichier, feuille, nombre de lignes de titre à sauter) est celle qu'a lue KLDataViz ; le
chemin du fichier est un paramètre à ajuster en tête de requête.

Différences assumées, écrites en commentaire dans la requête quand elles s'appliquent :
- Power Query n'a pas d'expressions régulières : un motif de lignes à retirer devient « contient ce texte »
  (sans tenir compte des majuscules) ; les totaux et sous-totaux ont leur propre test, équivalent.
- Les nombres convertis le sont avec la culture « fr-FR » (virgule décimale, espace des milliers).
- Les informations collées (indicateur, période, sexe, âge) ne sont pas re-reconnues dans Excel : la requête emporte
  la table de correspondance calculée par KLDataViz (titre ou valeur → informations). Une valeur absente de cette table
  (nouvelle catégorie le mois suivant) reste vide dans Excel, et la requête le dit en commentaire."""
import datetime as dt
import re

import recettes

AGG_M = {"somme": "List.Sum", "moyenne": "List.Average", "min": "List.Min", "max": "List.Max", "mediane": "List.Median"}
TYPES_M = {"nombre": "type number", "date": "type date", "texte": "type text"}


def _s(t):
    """Chaîne M : guillemets doublés."""
    return '"' + str(t).replace('"', '""') + '"'


def _liste(xs):
    return "{" + ", ".join(_s(x) for x in xs) + "}"


def _nom_etape(i, e):
    """Nom d'étape lisible, comme dans « Étapes appliquées » : la phrase de l'étape (Power Query accepte tout texte
    entre #"…", guillemets doublés)."""
    t = " ".join(recettes.libelle(e).split())
    t = t if len(t) <= 70 else t[:69].rstrip() + "…"
    return '#"' + f"{i}. {t}".replace('"', '""') + '"'


def _total_m():
    """Les lignes de total et de sous-total, comme recettes.TOTAL, sans expression régulière : le premier mot (après
    « sous- » ou « grand ») est total, totale, totals ou totaux ; « Totalité » n'en est pas une, comme dans KLDataViz."""
    return ('(v) => v is text and (let t = Text.Lower(Text.Trim(v)), '
            'm = List.Select(Text.SplitAny(Text.Replace(t, "grand ", ""), " :;,.-"), each _ <> ""), '
            'p = if List.Count(m) > 1 and m{0} = "sous" then m{1} else if List.Count(m) > 0 then m{0} else "" '
            'in List.Contains({"total", "totale", "totals", "totaux"}, p) or t = "ensemble")')


def _condition(e):
    """Condition M (sur la ligne `_`) vraie pour les lignes visées par retirer_lignes / garder_lignes, et un commentaire."""
    if e.get("vides"):
        return 'List.IsEmpty(List.RemoveMatchingItems(Record.FieldValues(_), {"", null}))', ""
    if e.get("valeurs") is not None:
        vals = _liste(str(v).strip().lower() for v in e["valeurs"])
        test = f"(v) => v <> null and List.Contains({vals}, Text.Lower(Text.Trim(Text.From(v))))"
        note = ""
    elif e.get("motif") == recettes.TOTAL.pattern:
        test, note = _total_m(), ""
    else:
        motif, note = str(e.get("motif") or ""), ""
        if re.search(r"[\\^$.*+?()\[\]{}|]", motif):
            note = f"// motif « {motif} » : Power Query n'a pas d'expression régulière, lu comme « contient ce texte »"
            motif = re.sub(r"\.[*+?]", "", motif)                       # « .* » : n'importe quoi, donc rien
            motif = re.sub(r"[\\^$*+?()\[\]{}|]", "", motif)
        test = f"(v) => v <> null and Text.Contains(Text.Lower(Text.From(v)), {_s(motif.lower())})"
    if e.get("colonne"):
        return f"({test})(Record.Field(_, {_s(e['colonne'])}))", note
    return f"List.AnyTrue(List.Transform(Record.FieldValues(_), {test}))", note


def _m(v):
    """Valeur Python → littéral M (texte, ou null pour une case vide)."""
    return "null" if v is None or (isinstance(v, float) and v != v) else _s(v)


def _table(colonnes, lignes):
    return "#table(" + _liste(colonnes) + ", {" + ", ".join("{" + ", ".join(_m(v) for v in l) + "}" for l in lignes) + "})"


def _informations(e, prec, avant):
    """decomposer / extraire : la correspondance calculée par KLDataViz sur `avant` (le tableau avant l'étape) est
    jointe dans Power Query, puis les colonnes sont remises dans l'ordre de KLDataViz."""
    if avant is None:
        raise recettes.Erreur("Séparer les informations collées : l'export a besoin du fichier.")
    apres, _ = recettes.appliquer(avant, [e])
    ordre = [str(c) for c in apres.columns]
    if e["op"] == "decomposer":
        garder, ecartees, carte, _, val = recettes.plan_decomposer(avant, e)
        nouvelles = [c for c in ordre if c not in {str(x) for x in avant.columns} and c != val]
        corps = [f"let source = {prec},"]
        base = "source"
        if ecartees:
            corps.append(f"        sans_sous_totaux = Table.RemoveColumns(source, {_liste(ecartees)}),")
            base = "sans_sous_totaux"
        corps += [f'        lignes = Table.Unpivot({base}, {_liste(garder)}, "_titre", {_s(val)}),',
                  f'        remplies = Table.SelectRows(lignes, each Text.Trim(Text.From(Record.Field(_, {_s(val)}))) <> ""),',
                  f"        correspondance = {_table(['_cle'] + nouvelles, [[c] + [carte[c].get(n) for n in nouvelles] for c in garder])},",
                  '        jointes = Table.Join(remplies, "_titre", correspondance, "_cle"),',
                  '        propres = Table.RemoveColumns(jointes, {"_titre", "_cle"})',
                  f"    in Table.ReorderColumns(propres, {_liste(ordre)})"]
        note = "// sous-totaux écartés : recalculables à partir du détail" if ecartees else ""
        return "\n".join(corps), note
    col = e["colonne"]
    autres = {str(x) for x in avant.columns if str(x) != col}
    nouvelles = [c for c in ordre if c not in autres]
    vals = recettes._txt(avant[col]).str.strip()
    lignes = [[v] + [apres.loc[vals[vals == v].index[0], n] for n in nouvelles] for v in vals.unique() if v]
    corps = [f"let source = {prec},",
             f"        correspondance = {_table(['_cle'] + nouvelles, lignes)},",
             f'        jointes = Table.Join(source, {_s(col)}, correspondance, "_cle", JoinKind.LeftOuter),',
             f'        propres = Table.RemoveColumns(jointes, {{{_s(col)}, "_cle"}})',
             f"    in Table.ReorderColumns(propres, {_liste(ordre)})"]
    return "\n".join(corps), f"// « {col} » : une valeur absente de la correspondance (nouvelle catégorie) donne des cases vides"


def _etape(e, prec, avant=None):
    """(expression M, commentaire) pour une étape, appliquée à l'étape précédente `prec`. `avant` : le tableau tel
    qu'il est avant l'étape (pour les étapes qui emportent une table de correspondance)."""
    op = e["op"]
    if op in ("decomposer", "extraire"):
        return _informations(e, prec, avant)
    if op == "nettoyer_texte":
        espaces = '" #(tab)#(lf)#(cr)#(00A0)"'
        return (f"Table.TransformColumns({prec}, List.Transform(Table.ColumnNames({prec}), (c) => {{c, (v) => if v is text "
                f'then Text.Combine(List.Select(Text.SplitAny(v, {espaces}), each _ <> ""), " ") else v}}))'), ""
    if op == "recopier_bas":
        return f"Table.FillDown({prec}, {_liste(e['colonnes'])})", ""
    if op == "depivoter":
        if not e.get("colonnes"):
            raise recettes.Erreur("Dépivoter par motif : à exporter après avoir choisi les colonnes.")
        note = "" if not e.get("garder_vides") else "// Table.Unpivot écarte les valeurs vides : à ajuster si besoin"
        val = _s(e.get("valeur") or "valeur")
        expr = f"Table.Unpivot({prec}, {_liste(e['colonnes'])}, {_s(e.get('nom') or 'attribut')}, {val})"
        if not e.get("garder_vides"):  # comme KLDataViz : les cases vides "" sont aussi écartées (Unpivot ne retire que null)
            expr = f'Table.SelectRows({expr}, each Text.Trim(Text.From(Record.Field(_, {val}))) <> "")'
        return expr, note
    if op in ("retirer_lignes", "garder_lignes"):
        cond, note = _condition(e)
        garder = f"each {cond}" if op == "garder_lignes" else f"each not ({cond})"
        return f"Table.SelectRows({prec}, {garder})", note
    if op == "fractionner":
        noms = e.get("noms") or [f"{e['colonne']}_1", f"{e['colonne']}_2"]
        sep = e.get("separateur", " - ")
        sep_m = "Splitter.SplitTextByEachDelimiter(" + _liste([sep] * (len(noms) - 1)) + ", QuoteStyle.None)"
        if e.get("garder", False):
            copie = f"{e['colonne']} (copie)"
            return (f"Table.SplitColumn(Table.DuplicateColumn({prec}, {_s(e['colonne'])}, {_s(copie)}), {_s(copie)}, "
                    f"{sep_m}, {_liste(noms)})"), ""
        return f"Table.SplitColumn({prec}, {_s(e['colonne'])}, {sep_m}, {_liste(noms)})", ""
    if op == "renommer":
        paires = ", ".join("{" + _s(a) + ", " + _s(b) + "}" for a, b in e["colonnes"].items())
        return f"Table.RenameColumns({prec}, {{{paires}}})", ""
    if op == "retirer_colonnes":
        return f"Table.RemoveColumns({prec}, {_liste(e['colonnes'])})", ""
    if op == "remplacer":
        expr = prec
        for a, b in e["remplacements"].items():
            expr = f"Table.ReplaceValue({expr}, {_s(a)}, {_s(b)}, Replacer.ReplaceValue, {_liste([e['colonne']])})"
        return expr, ""
    if op == "convertir":
        t = TYPES_M.get(e["type"])
        if t is None:
            raise recettes.Erreur(f"Type inconnu : {e['type']}.")
        culture = ', "fr-FR"' if e["type"] in ("nombre", "date") else ""
        return f"Table.TransformColumnTypes({prec}, {{{{{_s(e['colonne'])}, {t}}}}}{culture})", ""
    if op == "regrouper":
        mesures = e.get("mesures") or {}
        if not mesures:
            aggs = '{{"nombre", each Table.RowCount(_), Int64.Type}}'
        else:
            parts = []
            for c, a in mesures.items():
                if a == "compte":
                    parts.append("{" + _s(c) + ", each List.Count(List.RemoveNulls(Table.Column(_, " + _s(c) + "))), Int64.Type}")
                else:
                    fn = AGG_M.get(a)
                    if fn is None:
                        raise recettes.Erreur(f"Calcul inconnu : {a}.")
                    parts.append("{" + _s(c) + ", each " + fn + "(Table.Column(_, " + _s(c) + ")), type nullable number}")
            aggs = "{" + ", ".join(parts) + "}"
        return f"Table.Group({prec}, {_liste(e['par'])}, {aggs})", ""
    raise recettes.Erreur(f"Opération inconnue : {op}.")


def source_m(source):
    """Étapes M de la source : le fichier lu par KLDataViz, la feuille, les lignes de titre sautées, les en-têtes."""
    lignes = []
    if source.get("type") == "excel":
        feuille = source.get("feuille")
        lignes.append(f"    Classeur = Excel.Workbook(File.Contents(Fichier), null, true),")
        if isinstance(feuille, str):
            lignes.append(f'    Feuille = Classeur{{[Item={_s(feuille)}, Kind="Sheet"]}}[Data],')
        else:
            lignes.append('    Feuille = Table.SelectRows(Classeur, each [Kind] = "Sheet"){' + str(int(feuille or 0)) + "}[Data],")
        brut = "Feuille"
    else:
        sep = source.get("separateur") or ","
        enc = 1252 if str(source.get("encodage") or "").lower() in ("cp1252", "latin-1", "latin1") else 65001
        lignes.append(f'    Texte = Csv.Document(File.Contents(Fichier), [Delimiter={_s(sep)}, Encoding={enc}, '
                      'QuoteStyle=QuoteStyle.Csv]),')
        brut = "Texte"
    n = int(source.get("entete") or 0)
    if n:
        lignes.append(f"    #\"Lignes de titre sautées\" = Table.Skip({brut}, {n}),")
        brut = '#"Lignes de titre sautées"'
    lignes.append(f"    #\"En-têtes\" = Table.PromoteHeaders({brut}, [PromoteAllScalars=true]),")
    return lignes, '#"En-têtes"'


def vers_m(etapes, source, nom="", df=None):
    """Requête Power Query complète (texte M) pour une recette et sa source. `df` : le fichier lu, avant recette
    (nécessaire aux étapes qui séparent des informations collées)."""
    corps, prec = source_m(source)
    avant = df
    for i, e in enumerate(etapes, 1):
        expr, note = _etape(e, prec, avant)
        if avant is not None:
            avant, _ = recettes.appliquer(avant, [e])
        if note:
            corps.append("    " + note)
        nom_e = _nom_etape(i, e)
        corps.append(f"    {nom_e} = {expr},")
        prec = nom_e
    corps[-1] = corps[-1].rstrip(",")
    chemin = source.get("chemin") or "C:\\chemin\\vers\\le\\fichier.xlsx"
    tete = [f"// Recette de transformation KLDataViz{' — ' + nom if nom else ''}",
            f"// Exportée le {dt.date.today():%d/%m/%Y}. Excel : Données → Obtenir des données → Autres sources → "
            "Requête vide → Éditeur avancé, puis coller.",
            "// Ajustez le chemin du fichier ci-dessous.",
            "let",
            f"    Fichier = {_s(chemin)},"]
    return "\n".join(tete + corps + ["in", f"    {prec}"]) + "\n"
