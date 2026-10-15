"""Rapport PDF détaillé d'une analyse KLDataViz.

Construit à partir du dictionnaire renvoyé par `server.analyze` : fiche du fichier, synthèse,
qualité des données, graphiques (dessinés avec reportlab.graphics, sans matplotlib) accompagnés
d'une interprétation, statistiques, relations entre variables, anomalies et méthodologie.
Les constats de l'IA, s'ils ont été calculés dans l'interface, sont repris sous `_insights`.
"""
import math
import os
import re
import time
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.doughnut import Doughnut
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.lineplots import ScatterPlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (CondPageBreak, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

# --------------------------------------------------------------------------- #
# Charte
# --------------------------------------------------------------------------- #
GREEN = colors.HexColor("#1D7A4C")
GREEN_L = colors.HexColor("#E8F3EC")
INK = colors.HexColor("#1B2420")
MUTED = colors.HexColor("#5B6B63")
LINE = colors.HexColor("#D5DED9")
AMBER = colors.HexColor("#B45309")
AMBER_L = colors.HexColor("#FEF3E2")
RED = colors.HexColor("#B42318")
RED_L = colors.HexColor("#FDECEA")
SERIES = [colors.HexColor(c) for c in ("#1D7A4C", "#2563EB", "#D97706", "#7C3AED", "#0891B2", "#DB2777", "#65A30D", "#64748B")]

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_FONTS = False


def _fonts():
    """Vera (fournie avec reportlab) couvre les accents, « », —, … : plus de caractères manquants."""
    global _FONTS
    if _FONTS:
        return
    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("Vera-Bold", "VeraBd.ttf"))
    pdfmetrics.registerFont(TTFont("Vera-Italic", "VeraIt.ttf"))
    pdfmetrics.registerFont(TTFont("Vera-BoldItalic", "VeraBI.ttf"))
    pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="Vera-Bold", italic="Vera-Italic", boldItalic="Vera-BoldItalic")
    _FONTS = True


def _styles():
    base = dict(fontName="Vera", textColor=INK, leading=13, fontSize=9.2)
    return {
        "title": ParagraphStyle("title", fontName="Vera-Bold", fontSize=20, leading=25, textColor=colors.white),
        "cover_sub": ParagraphStyle("cover_sub", fontName="Vera", fontSize=10.5, leading=15, textColor=colors.white),
        "h1": ParagraphStyle("h1", fontName="Vera-Bold", fontSize=13.5, leading=18, textColor=GREEN, spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName="Vera-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", spaceAfter=4, **base),
        "small": ParagraphStyle("small", fontName="Vera", fontSize=7.8, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName="Vera", fontSize=7.8, leading=9.6, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName="Vera-Bold", fontSize=7.8, leading=9.6, textColor=colors.white),
        "bullet": ParagraphStyle("bullet", leftIndent=11, bulletIndent=2, spaceAfter=3, **base),
        "interp": ParagraphStyle("interp", fontName="Vera", fontSize=8.8, leading=12.4, textColor=INK,
                                 backColor=GREEN_L, borderPadding=(5, 6, 5, 6), spaceBefore=4, spaceAfter=10),
        "kpi_v": ParagraphStyle("kpi_v", fontName="Vera-Bold", fontSize=15, leading=18, textColor=GREEN, alignment=TA_CENTER),
        "kpi_l": ParagraphStyle("kpi_l", fontName="Vera", fontSize=7.5, leading=9.5, textColor=MUTED, alignment=TA_CENTER),
    }


# --------------------------------------------------------------------------- #
# Mise en forme des nombres et textes
# --------------------------------------------------------------------------- #
def nf(v, dec=None):
    """Nombre à la française : espace insécable pour les milliers, virgule décimale."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if dec is None:
        dec = 0 if x == int(x) else (1 if abs(x) >= 100 else 2)
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s


def pct(v, dec=1):
    return "—" if v is None else f"{nf(v, dec)} %"


def esc(s):
    return escape(str(s if s is not None else ""))


def md(s):
    """**gras** des textes de l'IA → <b>."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s))


def pretty(name):
    return str(name).replace("_", " ").strip()


DTYPE_FR = [("datetime", "Date"), ("bool", "Oui / Non"), ("int", "Nombre entier"), ("float", "Nombre décimal"),
            ("category", "Catégorie"), ("object", "Texte"), ("str", "Texte"), ("string", "Texte")]


def dtype_fr(dt):
    d = str(dt).lower()
    for k, v in DTYPE_FR:
        if k in d:
            return v
    return d


FILE_TYPES = {".csv": "CSV — texte délimité", ".txt": "Texte délimité", ".tsv": "TSV — texte délimité par tabulations",
              ".xlsx": "Classeur Excel (.xlsx)", ".xlsm": "Classeur Excel avec macros (.xlsm)",
              ".xls": "Classeur Excel 97-2003 (.xls)", ".json": "JSON", ".parquet": "Parquet (format colonne)"}


def file_type(name):
    ext = os.path.splitext(str(name or ""))[1].lower()
    if str(name).startswith(("http://", "https://")):
        return "Source en ligne (lien)"
    return FILE_TYPES.get(ext, (ext[1:].upper() + " ") if ext else "Inconnu")


# --------------------------------------------------------------------------- #
# Interprétations (règles, uniquement à partir des chiffres de l'analyse)
# --------------------------------------------------------------------------- #
def _vals(chart, i=0):
    try:
        return [float(x) if x is not None else 0.0 for x in chart["values"][i]]
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def interpret_chart(c):
    kind, typ = c.get("kind"), c.get("type")
    labels = [_period_label(x) for x in c.get("labels") or []]
    names = c.get("seriesNames") or []
    out = []
    if kind == "trend" and typ == "line":
        for i, name in enumerate(names[:3]):
            v = _vals(c, i)
            if len(v) < 2:
                continue
            first, last = v[0], v[-1]
            hi, lo = max(v), min(v)
            ihi, ilo = v.index(hi), v.index(lo)
            chg = (last - first) / first * 100 if first else None
            sens = "progresse" if chg and chg > 5 else "recule" if chg and chg < -5 else "reste globalement stable"
            s = f"<b>{esc(name)}</b> {sens} entre la première et la dernière période (de {nf(first)} à {nf(last)}"
            s += f", soit {'+' if chg >= 0 else ''}{nf(chg, 1)} %)." if chg is not None else ")."
            s += f" Le pic est atteint en <b>{esc(labels[ihi])}</b> ({nf(hi)}), le niveau le plus bas en {esc(labels[ilo])} ({nf(lo)})."
            mean = sum(v) / len(v)
            if mean and hi > 1.5 * mean:
                s += f" Le pic dépasse de {nf((hi / mean - 1) * 100, 0)} % la moyenne de la période : à vérifier (activité exceptionnelle ou erreur de saisie)."
            out.append(s)
    elif kind in ("ranking", "report") or (kind == "trend" and typ == "bar"):
        v = _vals(c)
        if len(names) > 1 and len(c.get("values") or []) > 1:  # barres groupées (ex. âge × sexe)
            tots = [sum(_vals(c, i)) for i in range(len(names))]
            tt = sum(tots) or 1
            parts = ", ".join(f"{esc(n)} {pct(t / tt * 100)}" for n, t in zip(names, tots))
            out.append(f"Répartition du total par série : {parts}.")
            per = [sum(_vals(c, i)[j] for i in range(len(names)) if j < len(_vals(c, i))) for j in range(len(labels))]
            if per:
                j = per.index(max(per))
                out.append(f"La catégorie la plus représentée est <b>{esc(labels[j])}</b> ({nf(per[j])}, {pct(per[j] / tt * 100)} du total).")
        elif v:
            tot = sum(v) or 1
            pairs = sorted(zip(labels, v), key=lambda x: -x[1])
            (l1, v1), (ln, vn) = pairs[0], pairs[-1]
            s = f"<b>{esc(l1)}</b> arrive en tête avec {nf(v1)}, soit {pct(v1 / tot * 100)} du total affiché"
            s += f" ; <b>{esc(ln)}</b> ferme la marche avec {nf(vn)}." if len(pairs) > 1 else "."
            if len(pairs) > 1 and vn > 0:
                s += f" L'écart entre le premier et le dernier est de 1 à {nf(v1 / vn, 1)}."
            out.append(s)
            ref = (c.get("ref") or {}).get("value")
            if ref:
                above = sum(1 for x in v if x > ref)
                out.append(f"{above} élément(s) sur {len(v)} se situent au-dessus de la moyenne ({nf(ref)}).")
            if len(pairs) > 3:
                top3 = sum(x for _, x in pairs[:3])
                out.append(f"Les 3 premiers concentrent {pct(top3 / tot * 100)} du total.")
    elif kind == "share":
        v = _vals(c)
        if v:
            tot = sum(v) or 1
            pairs = sorted(zip(labels, v), key=lambda x: -x[1])
            s = f"<b>{esc(pairs[0][0])}</b> représente la plus grande part ({pct(pairs[0][1] / tot * 100)})."
            if len(pairs) > 1:
                s += f" Les deux premières catégories cumulent {pct((pairs[0][1] + pairs[1][1]) / tot * 100)}."
            if len(pairs) > 2:
                s += f" La plus petite part est {esc(pairs[-1][0])} ({pct(pairs[-1][1] / tot * 100)})."
            out.append(s)
    elif kind == "distribution":
        v = _vals(c)
        if v:
            tot = sum(v) or 1
            j = v.index(max(v))
            s = f"La tranche la plus fréquente est <b>{esc(labels[j])}</b> : {nf(v[j])} ligne(s), soit {pct(v[j] / tot * 100)}."
            half = len(v) // 2
            left = sum(v[:half])
            if left / tot > 0.65:
                s += " Les valeurs se concentrent dans les tranches basses, avec quelques valeurs élevées qui étirent la distribution vers la droite."
            elif left / tot < 0.35:
                s += " Les valeurs se concentrent dans les tranches hautes."
            out.append(s)
    elif kind == "relation":
        m = re.search(r"r\s*=\s*(-?[\d,\.]+)", c.get("subtitle", ""))
        r = float(m.group(1).replace(",", ".")) if m else None
        if r is not None:
            force = "forte" if abs(r) >= 0.7 else "modérée" if abs(r) >= 0.4 else "faible"
            sens = "augmente" if r > 0 else "diminue"
            out.append(f"Relation {force} (r = {nf(r, 2)}) : quand {esc(c.get('xTitle', 'la première variable'))} augmente, "
                       f"{esc(c.get('yTitle', 'la seconde'))} {sens} en général. Une corrélation ne prouve pas un lien de cause à effet.")
    return " ".join(out)


TOTAL_RE = re.compile(r"^\s*(sous[-\s]?)?total(e|s|aux)?(\s+g[ée]n[ée]ral)?\s*$|^\s*(ensemble|grand total|tous|all|global)\s*$", re.I)


def drop_totals(charts):
    """Retire des classements et répartitions les lignes de cumul (« Total »…) : ce ne sont pas des catégories."""
    out, found = [], set()
    for c in charts:
        labels = [str(x) for x in c.get("labels") or []]
        idx = [i for i, l in enumerate(labels) if TOTAL_RE.match(l)]
        if idx and c.get("kind") in ("ranking", "share", "report") and len(labels) - len(idx) >= 2:
            keep = [i for i in range(len(labels)) if i not in idx]
            first = (c.get("values") or [[]])[0]
            tot_val = sum(float(first[i] or 0) for i in idx if i < len(first))
            ref = c.get("ref")
            c = dict(c, labels=[c["labels"][i] for i in keep],
                     values=[[v[i] for i in keep] for v in c.get("values") or []])
            if ref and ref.get("value") is not None:
                # La moyenne d'origine porte sur tous les groupes (« 12 premiers sur 25 ») : on en retire le cumul.
                m = re.search(r"sur (\d+)", c.get("subtitle") or "")
                n_all = int(m.group(1)) if m else len(labels)
                if n_all > len(idx):
                    c["ref"] = dict(ref, value=(ref["value"] * n_all - tot_val) / (n_all - len(idx)))
            c["subtitle"] = (c.get("subtitle") or "") + " · ligne de cumul écartée"
            found.update(labels[i] for i in idx)
        out.append(c)
    return out, sorted(found)


def quality_verdict(p):
    if p is None:
        return "non mesurée"
    return "excellente" if p >= 99 else "bonne" if p >= 95 else "acceptable" if p >= 85 else "insuffisante"


def stat_comment(name, v):
    mean, med, sd = v.get("mean"), v.get("median"), v.get("std")
    parts = []
    if mean is not None and med not in (None, 0):
        gap = (mean - med) / abs(med) * 100
        if gap > 25:
            parts.append(f"moyenne supérieure de {nf(gap, 0)} % à la médiane : quelques valeurs élevées tirent la moyenne")
        elif gap < -25:
            parts.append(f"moyenne inférieure de {nf(-gap, 0)} % à la médiane : quelques valeurs faibles tirent la moyenne")
        else:
            parts.append("distribution plutôt symétrique")
    if mean not in (None, 0) and sd is not None:
        cv = sd / abs(mean) * 100
        parts.append(f"dispersion {'très forte' if cv > 100 else 'forte' if cv > 50 else 'modérée' if cv > 20 else 'faible'} (CV {nf(cv, 0)} %)")
    if v.get("outliers"):
        parts.append(f"{v['outliers']} valeur(s) hors norme")
    return " ; ".join(parts)


# --------------------------------------------------------------------------- #
# Graphiques
# --------------------------------------------------------------------------- #
def _period_label(s):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})/\d{4}-\d{2}-\d{2}$", str(s))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)[2:]}" if m else str(s)


def _short(s, n=22):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _nice_max(v):
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def _axis_fmt(x):
    ax = abs(x)
    if ax >= 1e6:
        return nf(x / 1e6, 1) + " M"
    if ax >= 1e4:
        return nf(x / 1e3, 0) + " k"
    return nf(x, 0 if x == int(x) else 1)


def _legend(d, names, x, y, cols=4):
    lg = Legend()
    lg.x, lg.y = x, y
    lg.fontName, lg.fontSize = "Vera", 7
    lg.alignment = "right"
    lg.columnMaximum = len(names)
    lg.dxTextSpace, lg.dx, lg.dy = 4, 7, 7
    lg.deltax = 90
    lg.colorNamePairs = [(SERIES[i % len(SERIES)], _short(n, 24)) for i, n in enumerate(names)]
    d.add(lg)


def draw_line(c, w=CONTENT_W, h=200):
    labels = [str(x) for x in c.get("labels") or []]
    names = c.get("seriesNames") or [""]
    series = [_vals(c, i) for i in range(len(names))]
    # Deux échelles très différentes : chaque série est ramenée à son propre maximum pour rester lisible.
    rescale = bool(c.get("axis2")) and len(series) > 1
    d = Drawing(w, h)
    ch = HorizontalLineChart()
    ch.x, ch.y, ch.width, ch.height = 42, 46, w - 60, h - 70
    data = [[x / (max(s) or 1) * 100 for x in s] for s in series] if rescale else series
    ch.data = data
    ch.joinedLines = 1
    top = _nice_max(max(max(s) for s in data if s) if any(data) else 1)
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = 0, top, top / 4
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = "Vera", 7
    ch.valueAxis.labelTextFormat = (lambda x: f"{x:.0f} %") if rescale else _axis_fmt
    ch.valueAxis.gridStrokeColor, ch.valueAxis.visibleGrid = LINE, 1
    ch.categoryAxis.categoryNames = [_short(_period_label(l), 14) for l in labels]
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Vera", 6.5
    if len(labels) > 6:
        ch.categoryAxis.labels.angle, ch.categoryAxis.labels.boxAnchor = 30, "ne"
    ch.categoryAxis.strokeColor = ch.valueAxis.strokeColor = LINE
    for i in range(len(data)):
        ch.lines[i].strokeColor = SERIES[i % len(SERIES)]
        ch.lines[i].strokeWidth = 1.8
        ch.lines[i].symbol = makeMarker("FilledCircle", size=3.2, fillColor=SERIES[i % len(SERIES)])
    d.add(ch)
    _legend(d, names, w - 10, h - 8)
    if rescale:
        d.add(String(42, h - 12, "Échelle : % du maximum de chaque série", fontName="Vera-Italic", fontSize=6.5, fillColor=MUTED))
    return d


def draw_hbar(c, w=CONTENT_W):
    labels = [str(x) for x in c.get("labels") or []][:15]
    v = _vals(c)[:15]
    n = len(v)
    h = max(110, 22 * n + 50)
    d = Drawing(w, h)
    ch = HorizontalBarChart()
    left = min(150, 6 + 5.2 * max(len(_short(l, 30)) for l in labels)) if labels else 60
    ch.x, ch.y, ch.width, ch.height = left, 24, w - left - 40, h - 36
    ch.data = [list(reversed(v))]  # reportlab dessine de bas en haut : le premier en haut
    top = _nice_max(max(v) if v else 1)
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = 0, top, top / 4
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = "Vera", 7
    ch.valueAxis.labelTextFormat = _axis_fmt
    ch.valueAxis.visibleGrid, ch.valueAxis.gridStrokeColor = 1, LINE
    ch.categoryAxis.categoryNames = [_short(l, 30) for l in reversed(labels)]
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Vera", 7
    ch.categoryAxis.strokeColor = ch.valueAxis.strokeColor = LINE
    ch.bars[0].fillColor, ch.bars[0].strokeColor = GREEN, None
    ch.barLabelFormat = _axis_fmt
    ch.barLabels.fontName, ch.barLabels.fontSize, ch.barLabels.nudge = "Vera", 6.8, 10
    ch.barLabels.boxAnchor = "w"
    d.add(ch)
    ref = (c.get("ref") or {}).get("value")
    if ref and top:
        x = ch.x + ch.width * ref / top
        d.add(Line(x, ch.y - 2, x, ch.y + ch.height + 2, strokeColor=AMBER, strokeWidth=1.2, strokeDashArray=[3, 2]))
        d.add(String(x + 3, ch.y + ch.height + 3, f"{c['ref'].get('label', 'Moyenne')} : {_axis_fmt(ref)}",
                     fontName="Vera", fontSize=6.8, fillColor=AMBER))
    return d


def draw_vbar(c, w=CONTENT_W, h=200):
    labels = [str(x) for x in c.get("labels") or []][:24]
    names = c.get("seriesNames") or [""]
    data = [_vals(c, i)[:24] for i in range(len(names))]
    d = Drawing(w, h)
    ch = VerticalBarChart()
    ch.x, ch.y, ch.width, ch.height = 42, 48, w - 60, h - 72
    ch.data = data
    top = _nice_max(max((max(s) for s in data if s), default=1))
    ch.valueAxis.valueMin, ch.valueAxis.valueMax, ch.valueAxis.valueStep = 0, top, top / 4
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = "Vera", 7
    ch.valueAxis.labelTextFormat = _axis_fmt
    ch.valueAxis.visibleGrid, ch.valueAxis.gridStrokeColor = 1, LINE
    ch.categoryAxis.categoryNames = [_short(l, 14) for l in labels]
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Vera", 6.5
    if len(labels) > 7:
        ch.categoryAxis.labels.angle, ch.categoryAxis.labels.boxAnchor = 30, "ne"
    ch.categoryAxis.strokeColor = ch.valueAxis.strokeColor = LINE
    ch.groupSpacing, ch.barSpacing = 6, 1
    for i in range(len(data)):
        ch.bars[i].fillColor, ch.bars[i].strokeColor = SERIES[i % len(SERIES)], None
    if len(data) == 1 and len(labels) <= 12:
        ch.barLabelFormat = _axis_fmt
        ch.barLabels.fontName, ch.barLabels.fontSize, ch.barLabels.nudge = "Vera", 6.5, 6
    d.add(ch)
    if len(names) > 1:
        _legend(d, names, w - 10, h - 8)
    return d


def draw_doughnut(c, w=CONTENT_W, h=180):
    labels = [str(x) for x in c.get("labels") or []][:8]
    v = _vals(c)[:8]
    tot = sum(v) or 1
    d = Drawing(w, h)
    dn = Doughnut()
    dn.x, dn.y, dn.width, dn.height = 30, 10, h - 20, h - 20
    dn.data = v
    dn.labels = None
    dn.innerRadiusFraction = 0.55
    for i in range(len(v)):
        dn.slices[i].fillColor = SERIES[i % len(SERIES)]
        dn.slices[i].strokeColor = colors.white
        dn.slices[i].strokeWidth = 1
    d.add(dn)
    lg = Legend()
    lg.x, lg.y = h + 40, h - 20
    lg.fontName, lg.fontSize = "Vera", 8
    lg.columnMaximum, lg.dy, lg.deltay = 8, 8, 16
    lg.colorNamePairs = [(SERIES[i % len(SERIES)], f"{_short(l, 32)} — {nf(x)} ({nf(x / tot * 100, 1)} %)") for i, (l, x) in enumerate(zip(labels, v))]
    d.add(lg)
    return d


def draw_scatter(c, w=CONTENT_W, h=210):
    xs = [float(x) for x in c.get("labels") or []]
    ys = _vals(c)
    pts = [(x, y) for x, y in zip(xs, ys)][:400]
    d = Drawing(w, h)
    sp = ScatterPlot()
    sp.x, sp.y, sp.width, sp.height = 48, 40, w - 70, h - 60
    sp.data = [pts]
    sp.lines[0].strokeColor = None
    sp.lines[0].symbol = makeMarker("FilledCircle", size=3, fillColor=colors.Color(0.11, 0.48, 0.30, alpha=0.55))
    for ax in (sp.xValueAxis, sp.yValueAxis):
        ax.labels.fontName, ax.labels.fontSize = "Vera", 7
        ax.labelTextFormat = _axis_fmt
        ax.strokeColor = LINE
    sp.xLabel, sp.yLabel = "", ""
    sp.lineLabelFormat = None
    sp.yValueAxis.visibleGrid, sp.yValueAxis.gridStrokeColor = 1, LINE
    if len(pts) >= 3 and c.get("trend"):  # droite des moindres carrés
        n = len(pts)
        mx, my = sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        if sxx:
            b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
            a = my - b * mx
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            sp.data.append([(x0, a + b * x0), (x1, a + b * x1)])
            sp.lines[1].strokeColor, sp.lines[1].strokeWidth = AMBER, 1.3
            sp.lines[1].symbol = None
    d.add(sp)
    d.add(String(sp.x + sp.width / 2, 8, _short(c.get("xTitle", ""), 50), fontName="Vera", fontSize=7.5, fillColor=MUTED, textAnchor="middle"))
    d.add(String(8, sp.y + sp.height + 8, _short(c.get("yTitle", ""), 50), fontName="Vera", fontSize=7.5, fillColor=MUTED))
    return d


def draw_chart(c):
    typ = c.get("type")
    try:
        if typ == "line":
            return draw_line(c)
        if typ == "bar" and c.get("horizontal"):
            return draw_hbar(c)
        if typ in ("bar", "histogram"):
            return draw_vbar(c)
        if typ in ("doughnut", "pie"):
            return draw_doughnut(c)
        if typ == "scatter":
            return draw_scatter(c)
    except Exception:  # un graphique illisible ne doit jamais empêcher le rapport
        return None
    return None


# --------------------------------------------------------------------------- #
# Tableaux
# --------------------------------------------------------------------------- #
def table(headers, rows, widths=None, st=None, zebra=True, align_right=()):
    s = st or _styles()
    data = [[Paragraph(esc(h), s["cellb"]) for h in headers]]
    for r in rows:
        data.append([x if isinstance(x, Paragraph) else Paragraph(esc(x), s["cell"]) for x in r])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), GREEN),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if zebra:
        for i in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F6F9F7")))
    t.setStyle(TableStyle(style))
    return t


def kpi_row(items, st):
    cells = [[Paragraph(esc(v), st["kpi_v"]), Paragraph(esc(l), st["kpi_l"])] for v, l in items]
    t = Table([[Table([[a], [b]], colWidths=[CONTENT_W / len(items) - 8]) for a, b in cells]],
              colWidths=[CONTENT_W / len(items)] * len(items))
    t.setStyle(TableStyle([("BOX", (i, 0), (i, 0), 0.6, LINE) for i in range(len(items))] +
                          [("BACKGROUND", (0, 0), (-1, -1), colors.white),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def callout(text, st, level="info"):
    bg, bd = {"warn": (AMBER_L, AMBER), "bad": (RED_L, RED)}.get(level, (GREEN_L, GREEN))
    t = Table([[Paragraph(text, st["body"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 3, bd),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


# --------------------------------------------------------------------------- #
# En-tête, pied de page et numérotation « Page x / y »
# --------------------------------------------------------------------------- #
class _NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._pages)
        for p in self._pages:
            self.__dict__.update(p)
            if self._pageNumber > 1:
                self.setFont("Vera", 7.5)
                self.setFillColor(MUTED)
                self.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Page {self._pageNumber} / {n}")
            super().showPage()
        super().save()


def _decor(file_name, stamp):
    def first(cv, doc):
        cv.saveState()
        cv.setFillColor(GREEN)
        cv.rect(0, PAGE_H - 62 * mm, PAGE_W, 62 * mm, stroke=0, fill=1)
        cv.restoreState()

    def later(cv, doc):
        cv.saveState()
        cv.setFont("Vera-Bold", 7.5)
        cv.setFillColor(GREEN)
        cv.drawString(MARGIN, PAGE_H - 11 * mm, "KLDataViz · Rapport d'analyse")
        cv.setFont("Vera", 7.5)
        cv.setFillColor(MUTED)
        cv.drawRightString(PAGE_W - MARGIN, PAGE_H - 11 * mm, _short(file_name, 70))
        cv.setStrokeColor(LINE)
        cv.line(MARGIN, PAGE_H - 13 * mm, PAGE_W - MARGIN, PAGE_H - 13 * mm)
        cv.drawString(MARGIN, 10 * mm, f"Généré le {stamp} par KLDataViz — agent Data Analytics & BI")
        cv.restoreState()
    return first, later


# --------------------------------------------------------------------------- #
# Construction du rapport
# --------------------------------------------------------------------------- #
def build(result, path):
    _fonts()
    st = _styles()
    r = result or {}
    name = r.get("file") or "Fichier"
    stamp = time.strftime("%d/%m/%Y à %H:%M")
    cols = r.get("columns_detail") or []
    ns = r.get("numeric_stats") or {}
    bv = r.get("bivariate") or {}
    anomalies = r.get("anomalies") or []
    charts = [c for c in (r.get("smart_charts") or []) if c.get("type") in ("line", "bar", "histogram", "doughnut", "pie", "scatter")]
    charts, totals = drop_totals(charts)
    n_num = sum(1 for c in cols if dtype_fr(c.get("dtype")) in ("Nombre entier", "Nombre décimal"))
    n_date = sum(1 for c in cols if dtype_fr(c.get("dtype")) == "Date")
    n_txt = len(cols) - n_num - n_date
    rows_n = r.get("rows") or 0
    comp = r.get("completeness_pct")
    n_anom = sum(a.get("count", 0) for a in anomalies)
    corrs = bv.get("correlations") or [dict(c, force=None) for c in r.get("correlations") or []]
    insights = r.get("_insights") or []

    story = []
    # ---- Page de garde
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph("Rapport d'analyse de données", st["title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"<b>{esc(name)}</b><br/>{esc(file_type(name))} · généré le {stamp}", st["cover_sub"]))
    story.append(Spacer(1, 26 * mm))
    story.append(kpi_row([(nf(rows_n), "lignes analysées"), (nf(r.get("columns")), "colonnes"),
                          (pct(comp), "complétude"), (nf(n_anom), "valeurs hors norme")], st))
    story.append(Spacer(1, 8))

    # ---- 1. Fiche du fichier
    story.append(Paragraph("1. Fiche du fichier", st["h1"]))
    feuille = r.get("feuille") or {}
    fiche = [["Nom du fichier", name], ["Type de fichier", file_type(name)],
             ["Taille", f"{nf(r.get('size_kb'), 1)} Ko" if r.get("size_kb") else "—"],
             ["Dimensions", f"{nf(rows_n)} lignes × {nf(r.get('columns'))} colonnes ({nf(rows_n * (r.get('columns') or 0))} cellules)"],
             ["Composition des colonnes", f"{n_num} numérique(s) · {n_txt} texte / catégorie(s) · {n_date} date(s)"],
             ["Valeurs manquantes", f"{nf(r.get('missing_total'))} cellule(s) — complétude {pct(comp)}"],
             ["Version analysée", "Données corrigées par KLDataViz" if "_nettoye" in str(name) else
              "Structure proposée par l'architecte de données" if str(name).startswith("structure_") else "Données d'origine"],
             ["Date de l'analyse", stamp]]
    if feuille:
        fiche.insert(2, ["Feuille lue", f"{feuille.get('lue')}" + (f" (autres feuilles : {', '.join(map(str, feuille.get('autres') or []))})" if feuille.get("autres") else "")])
    t = Table([[Paragraph(f"<b>{esc(a)}</b>", st["cell"]), Paragraph(esc(b), st["cell"])] for a, b in fiche],
              colWidths=[48 * mm, CONTENT_W - 48 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (0, -1), GREEN_L),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(t)

    # ---- 2. Synthèse
    story.append(Paragraph("2. Synthèse des résultats", st["h1"]))
    pts = [f"Le fichier contient <b>{nf(rows_n)} lignes</b> et <b>{nf(r.get('columns'))} colonnes</b> ; "
           f"la complétude est <b>{quality_verdict(comp)}</b> ({pct(comp)})."]
    if anomalies:
        worst = max(anomalies, key=lambda a: a.get("count", 0))
        pts.append(f"<b>{nf(n_anom)} valeur(s) hors norme</b> détectée(s) dans {len(anomalies)} colonne(s), "
                   f"surtout dans <b>{esc(pretty(worst['column']))}</b> ({worst['count']}). Elles sont à vérifier avant interprétation.")
    else:
        pts.append("Aucune valeur hors norme n'a été détectée par la méthode des quartiles.")
    trend = next((c for c in charts if c.get("kind") == "trend" and c.get("type") == "line"), None)
    if trend:
        pts.append(interpret_chart(trend).split(". Le pic")[0] + ".")
    rank = next((c for c in charts if c.get("kind") == "ranking"), None)
    if rank:
        pts.append(f"{esc(rank.get('title'))} : " + interpret_chart(rank).split(" L'écart")[0].split(" ; ")[0] + ".")
    strong = [c for c in corrs if abs(c.get("r", 0)) >= 0.7 and abs(c.get("r", 0)) < 0.999]
    if strong:
        c0 = strong[0]
        pts.append(f"Relation la plus marquée : <b>{esc(pretty(c0['a']))}</b> et <b>{esc(pretty(c0['b']))}</b> (r = {nf(c0['r'], 2)}).")
    groups = bv.get("groupes") or []
    if groups:
        g = groups[0]
        pairs = sorted(zip(g.get("labels", []), g.get("moyennes", [])), key=lambda x: -(x[1] or 0))
        if len(pairs) >= 2 and pairs[-1][1]:
            pts.append(f"En moyenne, <b>{esc(pretty(g['num']))}</b> varie de {nf(pairs[-1][1])} ({esc(pairs[-1][0])}) à "
                       f"{nf(pairs[0][1])} ({esc(pairs[0][0])}) selon {esc(pretty(g['cat']))}, soit un rapport de 1 à {nf(pairs[0][1] / pairs[-1][1], 1)}.")
    if totals:
        pts.insert(1, f"Le fichier contient une ou plusieurs <b>lignes de cumul</b> ({', '.join('« ' + esc(t) + ' »' for t in totals)}) "
                      "mêlées aux lignes de détail. Elles ont été écartées des graphiques, mais elles faussent les totaux, moyennes, "
                      "corrélations et valeurs hors norme calculés sur l'ensemble des lignes : retirez-les du fichier pour une analyse exacte.")
    for p in pts:
        story.append(Paragraph(p, st["bullet"], bulletText="•"))
    if totals:
        story.append(callout("<b>Attention</b> — les statistiques des sections 5 à 7 incluent les lignes de cumul "
                             f"({', '.join(esc(t) for t in totals)}). Utilisez « Proposer une structure » dans KLDataViz pour les séparer automatiquement.", st, "warn"))
        story.append(Spacer(1, 4))
    if insights:
        story.append(Paragraph("Constats de l'IA (Hermes)", st["h2"]))
        for it in insights:
            lvl = it.get("severity") if it.get("severity") in ("warn", "bad") else "info"
            story.append(callout(f"<b>{esc(it.get('tag', 'Constat'))}</b> — {md(it.get('text'))}"
                                 + (f"<br/><font color='#5B6B63' size='8'>{md(it.get('detail'))}</font>" if it.get("detail") else ""), st, lvl))
            story.append(Spacer(1, 4))

    # ---- 3. Qualité des données
    story.append(PageBreak())
    story.append(Paragraph("3. Qualité et structure des données", st["h1"]))
    missing_cols = [c for c in cols if c.get("missing")]
    const_cols = [c for c in cols if c.get("unique", 2) <= 1]
    id_cols = [c for c in cols if rows_n and c.get("unique") == rows_n and dtype_fr(c.get("dtype")) == "Texte"]
    q = [f"Complétude globale : <b>{pct(comp)}</b> ({quality_verdict(comp)})."]
    q.append(f"{len(missing_cols)} colonne(s) comportent des valeurs manquantes" +
             (" : " + ", ".join(f"{esc(pretty(c['name']))} ({pct(c['missing_pct'])})" for c in sorted(missing_cols, key=lambda c: -c['missing_pct'])[:6]) + "." if missing_cols else "."))
    if const_cols:
        q.append(f"{len(const_cols)} colonne(s) ne prennent qu'une seule valeur et n'apportent pas d'information : "
                 + ", ".join(esc(pretty(c["name"])) for c in const_cols[:6]) + ".")
    if id_cols:
        q.append("Colonne(s) identifiant (une valeur différente par ligne) : " + ", ".join(esc(pretty(c["name"])) for c in id_cols[:4])
                 + ". Elles servent à repérer les lignes, pas à calculer.")
    for p in q:
        story.append(Paragraph(p, st["bullet"], bulletText="•"))
    story.append(Spacer(1, 4))
    story.append(table(["Colonne", "Type", "Valeurs distinctes", "Manquantes", "% manquant", "Rôle probable"],
                       [[pretty(c["name"]), dtype_fr(c["dtype"]), nf(c.get("unique")), nf(c.get("missing")), pct(c.get("missing_pct")),
                         "Identifiant" if c in id_cols else "Constante" if c in const_cols else
                         "Mesure" if dtype_fr(c["dtype"]) in ("Nombre entier", "Nombre décimal") else
                         "Période" if dtype_fr(c["dtype"]) == "Date" else "Dimension (axe d'analyse)"] for c in cols],
                       [42 * mm, 26 * mm, 22 * mm, 23 * mm, 19 * mm, CONTENT_W - 132 * mm]))
    cat = r.get("categorical_top") or {}
    if cat:
        story.append(Paragraph("Modalités les plus fréquentes", st["h2"]))
        rows = []
        for col, top in list(cat.items())[:8]:
            tot = rows_n or sum(top.values()) or 1
            items = list(top.items())[:5]
            rows.append([pretty(col), "  ·  ".join(f"{_short(k, 26)} ({nf(v)} ; {nf(v / tot * 100, 1)} %)" for k, v in items)])
        story.append(table(["Colonne", "Principales valeurs (effectif ; part des lignes)"], rows, [40 * mm, CONTENT_W - 40 * mm]))

    # ---- 4. Graphiques et interprétation
    if charts:
        story.append(PageBreak())
        story.append(Paragraph("4. Graphiques et interprétation", st["h1"]))
        story.append(Paragraph("Les graphiques ci-dessous sont choisis automatiquement selon la nature des colonnes. "
                               "Chacun est suivi d'une lecture des chiffres qu'il montre.", st["small"]))
        k = 0
        for c in charts:
            dr = draw_chart(c)
            if dr is None:
                continue
            k += 1
            block = [Paragraph(f"Figure {k} — {esc(c.get('title', ''))}", st["h2"])]
            if c.get("subtitle"):
                block.append(Paragraph(esc(c["subtitle"]), st["small"]))
            block.append(dr)
            txt = interpret_chart(c)
            if txt:
                block.append(Paragraph(f"<b>Lecture :</b> {txt}", st["interp"]))
            story.append(CondPageBreak(dr.height + 90))
            story.append(KeepTogether(block))

    # ---- 5. Statistiques descriptives
    if ns:
        story.append(CondPageBreak(160))
        story.append(Paragraph("5. Statistiques descriptives", st["h1"]))
        story.append(table(["Variable", "Moyenne", "Écart-type", "Min", "Médiane", "Max", "Hors norme"],
                           [[pretty(k), nf(v["mean"]), nf(v["std"]), nf(v["min"]), nf(v["median"]), nf(v["max"]), nf(v["outliers"])]
                            for k, v in ns.items()],
                           [46 * mm] + [(CONTENT_W - 46 * mm) / 6] * 6))
        story.append(Paragraph("Lecture variable par variable", st["h2"]))
        for k, v in list(ns.items())[:15]:
            story.append(Paragraph(f"<b>{esc(pretty(k))}</b> : {stat_comment(k, v)}.", st["bullet"], bulletText="•"))

    # ---- 6. Relations entre variables
    cramer = bv.get("cramer") or []
    if corrs or cramer or groups:
        story.append(CondPageBreak(160))
        story.append(Paragraph("6. Relations entre variables", st["h1"]))
    if corrs:
        story.append(Paragraph("Corrélations entre variables numériques (r de Pearson)", st["h2"]))
        def force(rv):
            a = abs(rv)
            return "très forte" if a >= 0.9 else "forte" if a >= 0.7 else "modérée" if a >= 0.4 else "faible"
        story.append(table(["Variable A", "Variable B", "r", "Force", "Sens"],
                           [[pretty(c["a"]), pretty(c["b"]), nf(c["r"], 2), force(c["r"]), "positif" if c["r"] > 0 else "négatif"] for c in corrs[:12]],
                           [50 * mm, 50 * mm, 18 * mm, 26 * mm, CONTENT_W - 144 * mm]))
        dup = [c for c in corrs if abs(c.get("r", 0)) >= 0.999]
        if dup:
            story.append(Paragraph("Une corrélation égale à 1 indique en général qu'une colonne est la somme ou la copie d'une autre "
                                   f"(ex. {esc(pretty(dup[0]['a']))} et {esc(pretty(dup[0]['b']))}) : ce n'est pas une découverte.", st["small"]))
    if cramer:
        story.append(Paragraph("Associations entre colonnes texte (V de Cramér)", st["h2"]))
        story.append(table(["Variable A", "Variable B", "V", "Lecture"],
                           [[pretty(c["a"]), pretty(c["b"]), nf(c["v"], 2),
                             "liées (l'une détermine l'autre)" if c["v"] >= 0.95 else "association forte" if c["v"] >= 0.5
                             else "association modérée" if c["v"] >= 0.3 else "pas d'association notable"] for c in cramer[:10]],
                           [50 * mm, 50 * mm, 18 * mm, CONTENT_W - 118 * mm]))
    if groups:
        story.append(Paragraph("Moyennes par groupe", st["h2"]))
        rows = []
        for g in groups[:6]:
            pairs = sorted(zip(g.get("labels", []), g.get("moyennes", [])), key=lambda x: -(x[1] or 0))
            if len(pairs) < 2:
                continue
            ratio = f"1 à {nf(pairs[0][1] / pairs[-1][1], 1)}" if pairs[-1][1] else "—"
            rows.append([pretty(g["num"]), pretty(g["cat"]), f"{_short(pairs[0][0], 22)} ({nf(pairs[0][1])})",
                         f"{_short(pairs[-1][0], 22)} ({nf(pairs[-1][1])})", ratio])
        if rows:
            story.append(table(["Mesure", "Selon", "Plus haute moyenne", "Plus basse moyenne", "Écart"], rows,
                               [32 * mm, 30 * mm, 46 * mm, 46 * mm, CONTENT_W - 154 * mm]))

    # ---- 7. Anomalies
    story.append(CondPageBreak(140))
    story.append(Paragraph("7. Valeurs hors norme et contrôles", st["h1"]))
    if anomalies:
        story.append(Paragraph("Une valeur est signalée lorsqu'elle sort de l'intervalle [Q1 − 1,5 × IQR ; Q3 + 1,5 × IQR], "
                               "où Q1 et Q3 sont les premier et troisième quartiles et IQR leur écart. "
                               "Une valeur signalée n'est pas forcément fausse : c'est une valeur à vérifier.", st["body"]))
        rows = []
        for a in anomalies:
            m = re.search(r"lo=(-?[\d.]+), hi=(-?[\d.]+)", a.get("detail", ""))
            if m:
                lo, hi = float(m.group(1)), float(m.group(2))
                interval = f"entre {nf(max(lo, 0) if lo < 0 else lo)} et {nf(hi)}"
                note = "plus de la moitié des valeurs sont identiques : toute autre valeur est signalée" if lo == hi else ""
            else:
                interval, note = a.get("detail", ""), ""
            share = f" ({nf(a['count'] / rows_n * 100, 1)} % des lignes)" if rows_n else ""
            rows.append([pretty(a["column"]), f"{nf(a['count'])}{share}", interval, note])
        story.append(table(["Colonne", "Valeurs signalées", "Valeurs attendues", "Remarque"], rows,
                           [42 * mm, 34 * mm, 42 * mm, CONTENT_W - 118 * mm]))
    else:
        story.append(Paragraph("Aucune valeur hors norme détectée.", st["body"]))
    ctr = r.get("controles") or []
    if ctr:
        story.append(Paragraph(f"Contrôles de cohérence : {len(ctr)} écart(s) entre totaux et désagrégations", st["h2"]))
        story.append(table(["Indicateur", "Site", "Contrôle", "Attendu", "Trouvé"],
                           [[_short(x.get("indicateur"), 40), x.get("site"), x.get("controle"), nf(x.get("attendu")), nf(x.get("trouve"))] for x in ctr[:40]],
                           [52 * mm, 34 * mm, 40 * mm, 22 * mm, CONTENT_W - 148 * mm]))

    # ---- 8. Méthodologie
    story.append(CondPageBreak(150))
    story.append(Paragraph("8. Méthodologie et définitions", st["h1"]))
    for term, d in [("Complétude", "part des cellules renseignées sur l'ensemble des cellules du fichier."),
                    ("Médiane", "valeur qui partage les lignes en deux moitiés ; moins sensible aux valeurs extrêmes que la moyenne."),
                    ("Coefficient de variation (CV)", "écart-type divisé par la moyenne : mesure la dispersion relative."),
                    ("r de Pearson", "corrélation linéaire entre deux mesures, de −1 à +1 ; 0 = pas de relation linéaire."),
                    ("V de Cramér", "force de l'association entre deux colonnes texte, de 0 (aucune) à 1 (totale)."),
                    ("Méthode IQR", "détection des valeurs hors norme à partir de l'écart interquartile.")]:
        story.append(Paragraph(f"<b>{term}</b> : {d}", st["bullet"], bulletText="•"))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Les interprétations de ce rapport sont calculées à partir des seuls chiffres de l'analyse. "
                           "Elles décrivent ce que montrent les données ; les causes restent à confirmer sur le terrain.", st["small"]))

    first, later = _decor(name, stamp)
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=18 * mm, bottomMargin=17 * mm,
                            title=f"Rapport d'analyse — {name}", author="KLDataViz")
    doc.build(story, onFirstPage=first, onLaterPages=later, canvasmaker=_NumberedCanvas)
    return path
