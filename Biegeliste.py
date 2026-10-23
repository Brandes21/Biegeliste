"""
Biegeliste aufräumen (SOFiSTiK STLI Stahl- und Mattenliste).

Für jede Biegeform mit Segmenttabelle (Nr. dx dy l >°) wird die kleine
Originalskizze samt Beschriftung entfernt, die Form vergrößert neu
gezeichnet und Längen/Winkel kollisionsfrei platziert.

Aufruf:
    python Biegeliste.py [--nur-ueberlappungen]
        bereinigt alle PDFs aus ./input und legt sie in ./output ab
    python Biegeliste.py liste.pdf [ausgabe.pdf] [--nur-ueberlappungen]
        bereinigt eine einzelne Datei
"""
import math
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

FONT_FILE = Path(r"C:\Windows\Fonts\consola.ttf")
FONT_SIZE = 8.5
LINE_WIDTH = 1.0
PAD = 1           # Mindestabstand zwischen Beschriftungen/Linien
MARGIN_X = 22        # Platz links/rechts der Form für Beschriftung
MARGIN_Y = 11        # Platz oben/unten der Form für Beschriftung
MAX_SHAPE_WIDTH = 175
OVERLAP_GAP = 3    # Abstand übereinanderliegender Schenkel (Übergreifung)

NUM = re.compile(r"^-?\d+(\.\d+)?$")
LABEL = re.compile(r"^(-?\d+(\.\d+)?°?|-\d+-)$")    # Maß, Winkel oder variabler Schenkel


# ---------------------------------------------------------------- Geometrie

def seg_hits_rect(p, q, r):
    """Schneidet die Strecke p-q das Rechteck r?"""
    if r.contains(p) or r.contains(q):
        return True
    edges = [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
    return any(_segs_cross(p, q, a, b) for a, b in edges)


def _segs_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def unit(v):
    n = math.hypot(v.x, v.y)
    return pymupdf.Point(v.x / n, v.y / n) if n > 1e-9 else pymupdf.Point(0, 0)


def cross(u, v):
    return u.x * v.y - u.y * v.x


def dot(u, v):
    return u.x * v.x + u.y * v.y


def separate_overlaps(pts, scale, gap=OVERLAP_GAP):
    """Schenkel, die auf einem früheren Schenkel liegen (Übergreifung bei
    geschlossenen Bügeln), um 'gap' parallel nach innen versetzen."""
    n = len(pts) - 1
    dirs = [unit(pts[k + 1] - pts[k]) for k in range(n)]
    centroid = pymupdf.Point(sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts))
    offs = [pymupdf.Point(0, 0)] * n
    for k in range(n):
        p, q, d = pts[k], pts[k + 1], dirs[k]
        nrm = pymupdf.Point(-d.y, d.x)
        for j in range(k - 1):                       # nur nicht benachbarte, frühere
            a, b = pts[j] + offs[j], pts[j + 1] + offs[j]
            if abs(cross(d, dirs[j])) > 0.05:
                continue                             # nicht parallel
            sep = dot(p - a, nrm)
            if abs(sep) >= gap or abs(sep) > 2.5 * scale:
                continue                             # schon getrennt bzw. weit weg
            ta, tb = sorted((dot(a - p, d), dot(b - p, d)))
            if min(abs(q - p), tb) - max(0.0, ta) < 1.0:
                continue                             # keine Überdeckung entlang der Linie
            side = math.copysign(1, sep) if abs(sep) > 0.2 else \
                math.copysign(1, dot(centroid - p, nrm))
            offs[k] = nrm * (side * gap - sep)
            break
    if not any(abs(o) > 0 for o in offs):
        return list(pts)
    # Knickpunkte neu: Schnitt der (versetzten) Nachbarschenkel
    out = [pts[0] + offs[0]]
    for i in range(1, n):
        P, u = pts[i - 1] + offs[i - 1], dirs[i - 1]
        Q, v = pts[i] + offs[i], dirs[i]
        c = cross(u, v)
        if abs(c) < 1e-6:
            out.append(pts[i] + (offs[i - 1] + offs[i]) * 0.5)
        else:
            out.append(P + u * (cross(Q - P, v) / c))
    out.append(pts[n] + offs[n - 1])
    return out


def rotate(v, deg):
    a = math.radians(deg)
    return pymupdf.Point(v.x * math.cos(a) - v.y * math.sin(a),
                         v.x * math.sin(a) + v.y * math.cos(a))


# ---------------------------------------------------------------- Beschriftung

class Labeler:
    def __init__(self, font, region, groups):
        self.font = font
        self.region = region
        self.groups = groups          # je Schenkel [(p, q), ...] (Bogen: viele Stücke)
        self.segments = [s for g in groups for s in g]   # alle Teilstrecken
        self.placed = []              # belegte Text-Rechtecke
        self.problems = []            # Beschriftungen ohne sauberen Platz

    def text_rect(self, text, center):
        w = self.font.text_length(text, FONT_SIZE)
        h = FONT_SIZE * 0.72
        return pymupdf.Rect(center.x - w / 2, center.y - h / 2,
                            center.x + w / 2, center.y + h / 2)

    def is_free(self, r):
        rp = r + (-PAD, -PAD, PAD, PAD)
        if not self.region.contains(r):
            return False
        if any(rp.intersects(o) for o in self.placed):
            return False
        return not any(seg_hits_rect(p, q, rp) for p, q in self.segments)

    def place(self, text, candidates, anchor, owner_ok=lambda r: True, preferred=lambda r: True):
        """candidates: Mittelpunkte in Präferenzreihenfolge.
        owner_ok: prüft, ob die Beschriftung eindeutig ihrem Element zugeordnet ist.
        preferred: strengere Prüfung, die zuerst versucht wird.
        Gibt (rect, leader) zurück; leader ist None oder (von, nach)."""
        for check in (preferred, lambda r: True):
            for c in candidates:
                r = self.text_rect(text, c)
                if self.is_free(r) and owner_ok(r) and check(r):
                    self.placed.append(r)
                    return r, None
        # Notlösung: nächste freie Stelle in der Region + Hinweislinie
        best = None
        step = 2.5
        y = self.region.y0
        while y <= self.region.y1:
            x = self.region.x0
            while x <= self.region.x1:
                c = pymupdf.Point(x, y)
                d = abs(c - anchor)
                if best is None or d < best[0]:
                    r = self.text_rect(text, c)
                    if self.is_free(r) and owner_ok(r):
                        best = (d, r)
                x += step
            y += step
        if best is None:  # gar kein Platz: trotzdem setzen, aber melden
            r = self.text_rect(text, candidates[0])
            self.placed.append(r)
            self.problems.append(f"kein freier Platz für '{text}'")
            return r, None
        r = best[1]
        self.placed.append(r)
        return r, (anchor, nearest_point_on_rect(r, anchor))


def nearest_point_on_rect(r, p):
    return pymupdf.Point(min(max(p.x, r.x0), r.x1), min(max(p.y, r.y0), r.y1))


def nearest_on_seg(c, p, q):
    v = q - p
    L2 = v.x * v.x + v.y * v.y
    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((c - p).x * v.x + (c - p).y * v.y) / L2))
    return p + v * t


def dist_point_seg(c, p, q):
    return abs(c - nearest_on_seg(c, p, q))


def dist_group(c, group):
    """Abstand zu einem Schenkel = Liste von Teilstrecken (Bogen: viele)."""
    return min(dist_point_seg(c, p, q) for p, q in group)


def closer_to_own_segment(k, groups):
    """Textmitte liegt näher an Schenkel k als an allen anderen Schenkeln."""
    def ok(r):
        c = (r.tl + r.br) / 2
        own = dist_group(c, groups[k])
        return all(own <= dist_group(c, g) + 0.5 for j, g in enumerate(groups) if j != k)
    return ok


def clear_of_other_segments(own, segs, clearance=8.0):
    """Abstand zu allen Schenkeln außer 'own' mindestens 'clearance'
    (verhindert Beschriftung in engen Spalten zwischen Schenkeln)."""
    def ok(r):
        probe = [r.tl, r.tr, r.bl, r.br, (r.tl + r.br) / 2]
        return all(min(dist_group(c, g) for c in probe) >= clearance
                   for j, g in enumerate(segs) if j not in own)
    return ok


def closer_to_own_vertex(k, pts):
    """Text liegt deutlich näher an Knick k als an allen anderen Knicken
    (Stabenden zählen nicht, dort steht kein Winkel)."""
    def ok(r):
        own = abs(nearest_point_on_rect(r, pts[k]) - pts[k])
        return all(own * 1.6 + 1 < abs(nearest_point_on_rect(r, p) - p)
                   for j, p in enumerate(pts) if j != k and 0 < j < len(pts) - 1)
    return ok


def offset_center(base, direction, dist, w, h):
    """Mittelpunkt eines w*h-Rechtecks, das in 'direction' mit Abstand
    'dist' von 'base' liegt."""
    half = abs(direction.x) * w / 2 + abs(direction.y) * h / 2
    return base + direction * (dist + half)


# ---------------------------------------------------------------- PDF lesen

def find_shapes(page):
    """Liefert je Biegeform: Polylinie, Segmenttabelle, Bereich."""
    spans = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    spans.append((pymupdf.Rect(s["bbox"]), s["text"]))

    drawings = page.get_drawings()
    # Alle Einzelstrecken (auch aus mehrteiligen Pfaden und Rechtecken):
    # manche Listen zeichnen das Tabellengitter zeilenweise als Pfade.
    segs = []
    for d in drawings:
        for i in d["items"]:
            if i[0] == "l":
                segs.append((i[1], i[2]))
            elif i[0] == "re":
                q = i[1]
                segs += [(q.tl, q.tr), (q.bl, q.br), (q.tl, q.bl), (q.tr, q.br)]
    hlines = [(a, b) for a, b in segs if abs(a.y - b.y) < 0.1 and abs(a.x - b.x) > 0.5]
    polylines = []
    for d in drawings:
        items = d["items"]
        if not items or any(i[0] != "l" for i in items):
            continue
        if (d.get("width") or 0) > 0.8:
            pts = [items[0][1]] + [i[2] for i in items]
            polylines.append((pymupdf.Rect(d["rect"]), pts))

    # Spalte "Biegeform": senkrechte Linien links/rechts davon
    col = None
    for r, t in spans:
        if t.strip() == "Biegeform":
            col = r
    if col is None:
        return []
    # Spaltentrenner = senkrechte Strecken, die die Kopfzeile "Biegeform" kreuzen
    ymid = (col.y0 + col.y1) / 2
    vx = sorted({round(a.x, 1) for a, b in segs
                 if abs(a.x - b.x) < 0.1 and min(a.y, b.y) < ymid < max(a.y, b.y)})
    if not any(x < col.x0 for x in vx) or not any(x > col.x0 + 100 for x in vx):
        print(f"  Warnung Seite {page.number + 1}: Spalte 'Biegeform' nicht erkannt - übersprungen")
        return []
    pos_col_x1 = next((r.x1 + 3 for r, t in spans if t.strip().startswith("Pos.")), 0)
    left = max(x for x in vx if x < col.x0)
    right = min(x for x in vx if x > col.x1 and x > col.x0 + 100)

    def spans_column(a, b):
        return min(a.x, b.x) <= left + 1 and max(a.x, b.x) >= right - 1

    shapes = []
    claimed = []
    for r, t in spans:
        if not t.startswith("Nr.") or not (left < r.x0 < right):
            continue
        table = read_table(spans, r)
        # Zeilenoberkante: nächste waagrechte Linie über der Tabelle
        tops = [a.y for a, b in hlines
                if min(a.x, b.x) <= left + 1 and max(a.x, b.x) >= right - 1
                and a.y < r.y0]
        if not tops:
            continue
        region = pymupdf.Rect(left + 1.5, max(tops) + 1.5, right - 1.5, r.y0 - 1.5)
        pos = next((t.strip() for lr, t in spans
                    if lr.x1 < pos_col_x1 and t.strip().isdigit()
                    and region.y0 - 2 < lr.y0 < region.y1), "?")
        polys = [p for p in polylines if region.contains(p[0])]
        if len(polys) != 1:
            print(f"  Warnung Seite {page.number + 1}, Pos. {pos}: "
                  f"{len(polys)} Linienzüge gefunden - übersprungen")
            continue
        claimed.append(polys[0][0])
        pts = polys[0][1]
        paths = group_pieces(pts, table)
        if paths is None:
            print(f"  Warnung Seite {page.number + 1}, Pos. {pos}: {len(pts)-1} Segmente, "
                  f"aber {len(table)} Tabellenzeilen - übersprungen")
            continue
        labels = [(lr, lt) for lr, lt in spans if region.intersects(lr)]
        # variable Schenkel sind in der Skizze als "-2-" statt mit Länge beschriftet
        variable = {int(m.group(1)) for _, lt in labels
                    if (m := re.fullmatch(r"-(\d+)-", lt.strip()))}
        shapes.append(dict(region=region, points=pts, table=table, labels=labels,
                           pos=pos, variable=variable, dims=[], paths=paths))

    # Katalogformen ohne Segmenttabelle (z.B. C1): Maße nur in den Spalten A-H,
    # die Originalbeschriftung wird übernommen und nur verschoben.
    for prect, pts in polylines:
        if len(pts) < 3 or prect in claimed or not (left < prect.x0 and prect.x1 < right):
            continue
        above = [a.y for a, b in hlines if spans_column(a, b) and a.y < prect.y0]
        below = [a.y for a, b in hlines if spans_column(a, b) and a.y > prect.y1]
        rows = [a.y for a, b in hlines if min(a.x, b.x) <= vx[0] + 1 and a.y < prect.y0]
        if not above or not below or not rows:
            continue
        top, bottom, row_top = max(above), min(below), max(rows)
        for lr, lt in spans:                         # z.B. "Bemerkung:" unter der Skizze
            if left < lr.x0 < right and prect.y1 + 2 < lr.y0 < bottom \
                    and not LABEL.match(lt.strip()):
                bottom = lr.y0
        region = pymupdf.Rect(left + 1.5, top + 1.5, right - 1.5, bottom - 1.5)
        pos = next((t.strip() for lr, t in spans
                    if lr.x1 < pos_col_x1 and t.strip().isdigit()
                    and row_top < lr.y0 < region.y1), "?")
        labels = [(lr, lt) for lr, lt in spans
                  if region.intersects(lr) and LABEL.match(lt.strip())]
        dims = [fmt_length(float(x)) for lr, lt in spans
                if left < lr.x0 < right and row_top < lr.y0 and lr.y1 <= top + 1
                for x in lt.split() if NUM.match(x)]
        shapes.append(dict(region=region, points=pts, table=None, labels=labels,
                           pos=pos, variable=set(), dims=dims))
    return shapes


def span_tokens(r, t):
    """Wörter eines Spans mit geschätzter x-Lage (Listen sind in Festbreitenschrift)."""
    cw = r.width / max(len(t), 1)
    for m in re.finditer(r"\S+", t):
        yield r.x0 + m.start() * cw, r.x0 + m.end() * cw, m.group()


def read_table(spans, header):
    """Zeilen unter 'Nr.': [(l, winkel_oder_None, nr_oder_None, radius_oder_None)].
    Hakenzeilen (Bügel) haben keine Nummer, nur 'l' und ggf. Winkel.
    Bogenzeilen haben zusätzlich r, Sehne, ab, Wink."""
    heads = {}
    for r, t in spans:
        if abs(r.y0 - header.y0) < 1 and header.x0 - 2 < r.x0 < header.x0 + 300:
            for x0, x1, w in span_tokens(r, t):
                heads.setdefault(w, (x0, x1))
    ang_head = next((v for w, v in heads.items() if w.startswith(">")), None)
    r_head = heads.get("r")
    rows = {}
    for r, t in spans:
        if r.y0 > (header.y0 + header.y1) / 2 and header.x0 - 2 < r.x0 < header.x0 + 140:
            rows.setdefault(round(r.y0, 0), []).extend(span_tokens(r, t))
    out = []
    nr = 0
    for y in sorted(rows):
        items = sorted(rows[y])
        if r_head:                                   # Sehne, ab, Wink ignorieren
            items = [i for i in items if i[1] <= r_head[1] + 2]
        toks = [w for _, _, w in items]
        if not toks or not all(NUM.match(x) for x in toks):
            break
        if len(toks) >= 4 and toks[0].isdigit() and int(toks[0]) == nr + 1:
            nr += 1
            ang = rad = None
            for x0, x1, w in items[4:]:
                if ang_head and r_head:              # Spalte nach rechter Kante
                    if x1 <= ang_head[1] + 2:
                        ang = w
                    else:
                        rad = float(w)
                elif ang is None:
                    ang = w
            out.append((float(toks[3]), ang, nr, rad))
        elif len(toks) <= 2 and items[0][0] > header.x0 + 60:   # Haken
            out.append((float(toks[0]), toks[1] if len(toks) > 1 else None, None, None))
        else:
            break
    return out


def group_pieces(pts, table):
    """Teilstrecken des Linienzugs den Tabellenzeilen zuordnen: gerader
    Schenkel = 1 Stück, Bogen (Zeile mit Radius) = mehrere kurze Stücke.
    Liefert je Zeile die Punktliste oder None, wenn es nicht aufgeht."""
    n = len(pts) - 1
    arcs = [i for i, row in enumerate(table) if row[3] is not None]
    if not arcs:
        return [[pts[i], pts[i + 1]] for i in range(n)] if n == len(table) else None
    remaining = n - (len(table) - len(arcs))
    if remaining < len(arcs):
        return None
    lens = [abs(pts[j + 1] - pts[j]) for j in range(n)]
    total_l = sum(row[0] for row in table)
    if total_l <= 0:
        return None
    scale = sum(lens) / total_l
    out, j = [], 0
    for i, row in enumerate(table):
        if row[3] is None:
            c = 1
        else:
            later = sum(1 for a in arcs if a > i)
            if not later:
                c = remaining
            else:                                    # Stücke nach Länge verteilen
                target, c, acc = row[0] * scale, 0, 0.0
                while c < remaining - later and (c == 0 or acc + lens[j + c] / 2 < target):
                    acc += lens[j + c]
                    c += 1
            remaining -= c
        glen = sum(lens[j:j + c])
        if abs(glen - row[0] * scale) > 0.25 * row[0] * scale + 2:
            return None                              # Längen passen nicht zur Tabelle
        out.append(pts[j:j + c + 1])
        j += c
    return out if j == n else None


def smooth_arc(pts):
    """Bogen als glatten Kreisbogen durch Anfang, Mitte und Ende neu erzeugen."""
    A, M, B = pts[0], pts[len(pts) // 2], pts[-1]
    D = 2 * (A.x * (M.y - B.y) + M.x * (B.y - A.y) + B.x * (A.y - M.y))
    if abs(D) < 1e-6:
        return list(pts)
    a2, m2, b2 = A.x ** 2 + A.y ** 2, M.x ** 2 + M.y ** 2, B.x ** 2 + B.y ** 2
    C = pymupdf.Point((a2 * (M.y - B.y) + m2 * (B.y - A.y) + b2 * (A.y - M.y)) / D,
                      (a2 * (B.x - M.x) + m2 * (A.x - B.x) + b2 * (M.x - A.x)) / D)
    R = abs(A - C)
    a0 = math.atan2(A.y - C.y, A.x - C.x)
    am = math.atan2(M.y - C.y, M.x - C.x)
    a1 = math.atan2(B.y - C.y, B.x - C.x)
    tau = 2 * math.pi
    sweep = (a1 - a0) % tau
    if (am - a0) % tau > sweep:                      # andere Richtung herum
        sweep -= tau
    steps = max(8, int(abs(sweep) * R / 2) + 1)
    out = [C + pymupdf.Point(math.cos(a0 + sweep * i / steps),
                             math.sin(a0 + sweep * i / steps)) * R for i in range(steps + 1)]
    out[0], out[-1] = A, B
    return out


def point_along(line, t):
    """Punkt und Richtung bei Anteil t der Länge eines Linienzugs."""
    pieces = list(zip(line[:-1], line[1:]))
    lens = [abs(q - p) for p, q in pieces]
    goal = sum(lens) * t
    for (p, q), L in zip(pieces, lens):
        if goal <= L:
            f = 0.0 if L < 1e-9 else goal / L
            return p + (q - p) * f, unit(q - p)
        goal -= L
    p, q = pieces[-1]
    return q, unit(q - p)


def has_overlap(labels):
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i][0], labels[j][0]
            inter = pymupdf.Rect(a) & b
            if not inter.is_empty and inter.width * inter.height > 1.0:
                return True
    return False


# ---------------------------------------------------------------- Neu zeichnen

def fmt_length(l):
    return f"{l:.2f}" if l >= 1.0 else f"{round(l * 100):d}"


def place_angle(lab, writer, font, new, k, text, leaders):
    """Winkelbeschriftung an den Knick k heften (bevorzugt außen an der Ecke)."""
    v = new[k]
    a = unit(lab.groups[k - 1][-1][0] - v)       # bei Bögen: Tangentenrichtung
    b = unit(lab.groups[k][0][1] - v)
    inside = unit(a + b)
    if abs(inside) < 1e-6:                           # gerader Stoß (0°)
        inside = pymupdf.Point(-a.y, a.x)
    outside = -inside
    w = font.text_length(text, FONT_SIZE)
    h = FONT_SIZE * 0.72
    cands = []
    for dist in (1.5, 4, 8, 13, 19):
        for rot in (0, 25, -25, 50, -50, 75, -75, 100, -100, 135, -135, 180):
            cands.append(offset_center(v, rotate(outside, rot), dist, w, h))
    r, leader = lab.place(text, cands, v, closer_to_own_vertex(k, new),
                          clear_of_other_segments({k - 1, k}, lab.groups))
    writer.append((r.x0, r.y1 - FONT_SIZE * 0.02), text, font=font, fontsize=FONT_SIZE)
    if leader:
        leaders.append(leader)


def place_original_labels(shape, lab, writer, font, transform, new, leaders):
    """Formen ohne Segmenttabelle: Originalbeschriftung übernehmen, mit der
    Form mitbewegen und nur so weit verschieben, bis nichts mehr überlappt."""
    pts = shape["points"]
    labels = [(lr, lt.strip()) for lr, lt in shape["labels"]]

    # Maße, die öfter in der Skizze stehen als in den Spalten A-H: nur das
    # Exemplar behalten, das am nächsten an einem Schenkel liegt
    allowed = Counter(shape["dims"])
    def seg_dist(lr):
        c = (lr.tl + lr.br) / 2
        return min(dist_point_seg(c, p, q) for p, q in zip(pts[:-1], pts[1:]))
    def nearest_seg(lr):
        c = (lr.tl + lr.br) / 2
        return min(range(len(pts) - 1), key=lambda j: dist_point_seg(c, pts[j], pts[j + 1]))
    keep = []
    for text in dict.fromkeys(t for _, t in labels):
        same = sorted((lr for lr, t in labels if t == text), key=seg_dist)
        if allowed[text] and not text.endswith("°"):
            # Überzählige Exemplare behalten, wenn sie an einem anderen, gleich
            # langen Schenkel stehen (symmetrische Formen wie C3 haben nur ein
            # Spaltenmaß für beide Schenkel)
            def seg_len(j):
                return abs(pts[j + 1] - pts[j])
            kept = same[:allowed[text]]
            used = {nearest_seg(lr) for lr in kept}
            for lr in same[allowed[text]:]:
                j = nearest_seg(lr)
                if j not in used and any(abs(seg_len(j) - seg_len(u)) <= 0.05 * max(seg_len(j), seg_len(u))
                                         for u in used):
                    kept.append(lr)
                    used.add(j)
            same = kept
        keep += [(lr, text) for lr in same]

    keep.sort(key=lambda e: not e[1].endswith("°"))   # Winkel zuerst
    for lr, text in keep:
        c = (lr.tl + lr.br) / 2
        if text.endswith("°") and len(pts) >= 3:
            # Winkel an den nächstgelegenen Knick heften
            k = min(range(1, len(pts) - 1), key=lambda j: abs(c - pts[j]))
            place_angle(lab, writer, font, new, k, text, leaders)
            continue
        # Maße am nächsten Punkt der Form verankern, Originalabstand zur Linie behalten
        foot = min((nearest_on_seg(c, p, q) for p, q in zip(pts[:-1], pts[1:])),
                   key=lambda f: abs(c - f))
        anchor = transform(foot) + (c - foot)
        cands = [anchor]
        for dist in (2, 4, 6, 9, 12, 16, 20):
            for ang in range(0, 360, 30):
                a = math.radians(ang)
                cands.append(anchor + pymupdf.Point(math.cos(a), math.sin(a)) * dist)
        r, leader = lab.place(text, cands, anchor)
        writer.append((r.x0, r.y1 - FONT_SIZE * 0.02), text, font=font, fontsize=FONT_SIZE)
        if leader:
            leaders.append(leader)


def redraw(page, shape, font, writer):
    region = shape["region"]
    pts = shape["points"]

    # Skalieren und in der Region zentrieren
    bb = pymupdf.Rect(pts[0], pts[0])
    for p in pts[1:]:
        bb |= p
    avail_w = min(region.width - 2 * MARGIN_X, MAX_SHAPE_WIDTH)
    avail_h = region.height - 2 * MARGIN_Y
    sx = avail_w / bb.width if bb.width > 1e-6 else math.inf
    sy = avail_h / bb.height if bb.height > 1e-6 else math.inf
    s = min(sx, sy)
    if not math.isfinite(s) or s <= 1.0:
        # Zelle zu klein zum Vergrößern: Originalgröße und -lage behalten
        s, ox, oy = 1.0, 0.0, 0.0
    else:
        # Form links in der Spalte, damit rechts Platz für Text bleibt
        ox = region.x0 + MARGIN_X - bb.x0 * s
        oy = region.y0 + (region.height - bb.height * s) / 2 - bb.y0 * s

    def transform(p):
        return pymupdf.Point(p.x * s + ox, p.y * s + oy)

    paths = shape.get("paths")
    if paths and any(len(g) > 2 for g in paths):
        # Form mit Bögen: Bögen glatt neu erzeugen, gerade Schenkel bleiben
        fine = [smooth_arc([transform(p) for p in g]) if len(g) > 2
                else [transform(g[0]), transform(g[-1])] for g in paths]
        new = [f[0] for f in fine] + [fine[-1][-1]]
    else:
        new = separate_overlaps([transform(p) for p in pts], s)
        fine = [[p, q] for p, q in zip(new[:-1], new[1:])]
    groups = [list(zip(f[:-1], f[1:])) for f in fine]   # Teilstrecken je Schenkel
    outline = [fine[0][0]] + [p for f in fine for p in f[1:]]

    shape_draw = page.new_shape()
    shape_draw.draw_polyline(outline)
    shape_draw.finish(color=(0, 0, 0), width=LINE_WIDTH, closePath=False,
                      lineCap=1, lineJoin=1)

    centroid = pymupdf.Point(sum(p.x for p in outline) / len(outline),
                             sum(p.y for p in outline) / len(outline))
    lab = Labeler(font, region, groups)
    h = FONT_SIZE * 0.72
    leaders = []

    if shape["table"] is None:
        place_original_labels(shape, lab, writer, font, transform, new, leaders)

    # 1) Winkel an die Knickpunkte (Winkel in Zeile i gilt am Ende von Segment i).
    #    Zuerst, weil ein Winkel nur um seinen Punkt herum ausweichen kann.
    table = shape["table"] or []
    for i, (_, ang, nr, _r) in enumerate(table):
        if ang is None or i + 1 >= len(new) - 0:
            continue
        # Hakenwinkel (Bügel) beschriftet SOFiSTiK nicht - wir auch nicht
        if nr is None or (i + 1 < len(table) and table[i + 1][2] is None):
            continue
        if i + 1 >= len(groups):
            continue
        place_angle(lab, writer, font, new, i + 1, f"{ang}°", leaders)

    # 2) Längen an die Schenkel
    for k, (line, (l, _, nr, _r)) in enumerate(zip(fine, table)):
        text = f"-{nr}-" if nr in shape["variable"] else fmt_length(l)
        w = font.text_length(text, FONT_SIZE)
        mid, d = point_along(line, 0.5)
        n = pymupdf.Point(-d.y, d.x)
        if (mid - centroid).x * n.x + (mid - centroid).y * n.y < 0:
            n = -n                                   # nach außen zeigen
        cands = []
        for dist in (1.5, 4, 8, 13):
            for t in (0.5, 0.35, 0.65, 0.2, 0.8):
                base, dt = point_along(line, t)      # bei Bögen: Punkt auf dem Bogen
                nt = pymupdf.Point(-dt.y, dt.x)
                if nt.x * n.x + nt.y * n.y < 0:
                    nt = -nt                         # gleiche Seite wie in der Mitte
                for side in (nt, -nt):
                    cands.append(offset_center(base, side, dist, w, h))
        r, leader = lab.place(text, cands, mid, closer_to_own_segment(k, groups),
                              clear_of_other_segments({k - 1, k, k + 1}, groups))
        writer.append((r.x0, r.y1 - FONT_SIZE * 0.02), text, font=font, fontsize=FONT_SIZE)
        if leader:
            leaders.append(leader)

    for a, b in leaders:
        shape_draw.draw_line(a, b)
    if leaders:
        shape_draw.finish(color=(0.3, 0.3, 0.3), width=0.4)
    shape_draw.commit()

    # Abschlusskontrolle: alles innerhalb der Zelle?
    problems = list(lab.problems)
    nb = pymupdf.Rect(outline[0], outline[0])
    for p in outline[1:]:
        nb |= p
    if not region.contains(nb):
        problems.append("Skizze ragt aus der Zelle")
    if any(not region.contains(r) for r in lab.placed):
        problems.append("Beschriftung ragt aus der Zelle")
    return problems


def process(src, dst, only_overlaps=False):
    doc = pymupdf.open(src)
    font = pymupdf.Font(fontfile=str(FONT_FILE)) if FONT_FILE.exists() else pymupdf.Font("cour")
    total = 0
    warnings = []
    for page in doc:
        shapes = find_shapes(page)
        todo = []
        for sh in shapes:
            overlap = has_overlap(sh["labels"])
            if only_overlaps and not overlap:
                continue
            todo.append((sh, overlap))
        if not todo:
            continue
        # erst alles entfernen, dann neu zeichnen
        for sh, _ in todo:
            page.add_redact_annot(sh["region"], fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
        writer = pymupdf.TextWriter(page.rect)
        for sh, overlap in todo:
            problems = redraw(page, sh, font, writer)
            total += 1
            print(f"  Seite {page.number + 1}, Pos. {sh['pos']}: neu gezeichnet"
                  f"{' (hatte Überlappungen)' if overlap else ''}")
            for msg in problems:
                warnings.append(f"Seite {page.number + 1}, Pos. {sh['pos']}: {msg}")
        writer.write_text(page)
    try:
        doc.save(dst, garbage=3, deflate=True)
    except Exception:
        print(f"FEHLER: {dst} kann nicht geschrieben werden - ist die Datei noch geöffnet?")
        return False
    print(f"{total} Biegeformen neu gezeichnet -> {dst}")
    if warnings:
        print("BITTE PRÜFEN:")
        for w in warnings:
            print("  " + w)
    else:
        print("Kontrolle: alle Skizzen und Beschriftungen liegen in ihrer Zelle.")
    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = "--nur-ueberlappungen" in sys.argv
    if args:
        src = Path(args[0])
        dst = Path(args[1]) if len(args) > 1 else src.with_name(src.stem + "_bereinigt.pdf")
        sys.exit(0 if process(src, dst, only_overlaps=only) else 1)

    # Stapelbetrieb: input -> output
    base = Path(__file__).resolve().parent
    in_dir, out_dir = base / "input", base / "output"
    in_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    pdfs = sorted(in_dir.glob("*.pdf"))
    if not pdfs:
        print(f"Keine PDF-Dateien in {in_dir}")
        sys.exit(0)
    failed = []
    for src in pdfs:
        print(f"\n=== {src.name} ===")
        try:
            ok = process(src, out_dir / (src.stem + "_bereinigt.pdf"), only_overlaps=only)
        except Exception as e:
            print(f"FEHLER: {e}")
            ok = False
        if not ok:
            failed.append(src.name)
    print(f"\n{len(pdfs) - len(failed)} von {len(pdfs)} Dateien bereinigt -> {out_dir}")
    if failed:
        print("Nicht verarbeitet: " + ", ".join(failed))
        sys.exit(1)
