"""Gruppen-Trophäen: dieselbe Xbox/PlayStation-Leiter wie bei den Karriere-Trophäen eines
einzelnen Spielers (siehe trophaeen.py), aber als KOLLEKTIVES Ziel über alle Mitglieder einer
Gruppe zusammen - gibt der Gruppe eine eigene Identität statt nur die Summe ihrer Mitglieder
zu sein, und liefert den Punktestand für die öffentliche Gruppen-Rangliste auf /gruppen
(siehe seiten_gruppen.oeffentliche_rangliste)."""

from achievements import DEATHLESS_MIN_BETEILIGUNG, DEATHLESS_MIN_DAUER
from trophaeen import FARBE_UNERREICHT, TIER_FARBE, TIER_LABEL, TIER_REIHENFOLGE, _stufe_fuer_wert

# Schwellwerte liegen deutlich über denen der Einzelspieler-Trophäen (trophaeen.py), weil hier
# die Werte ALLER Mitglieder zusammengezählt werden - grobe Schätzung, bei Bedarf später gegen
# die echte Verteilung in der DB nachjustieren.
GRUPPEN_TROPHAEEN_DEFINITIONEN = [
    {
        "id": "vereint", "icon": "🎮", "name": "Vereint",
        "beschreibung": "Gemeinsam bei RiftCircle getrackte Spiele (Summe aller Mitglieder)",
        "stufen": [100, 500, 1500, 4000, 10000],
    },
    {
        "id": "siegeszug", "icon": "🏆", "name": "Siegeszug",
        "beschreibung": "Gemeinsame Siege insgesamt",
        "stufen": [50, 250, 750, 2000, 5000],
    },
    {
        "id": "pentakill_arsenal", "icon": "🔥", "name": "Pentakill-Arsenal",
        "beschreibung": "Pentakills aller Mitglieder zusammen",
        "stufen": [3, 10, 25, 60, 120],
    },
    {
        "id": "makellos", "icon": "🛡️", "name": "Makellos",
        "beschreibung": "Deathless Spiele (kein einziger Tod) aller Mitglieder zusammen",
        "stufen": [10, 30, 80, 200, 400],
    },
    {
        "id": "turmsturm", "icon": "🏰", "name": "Turmsturm",
        "beschreibung": "Turm-Takedowns aller Mitglieder zusammen",
        "stufen": [300, 1200, 3000, 7500, 15000],
    },
    {
        "id": "fuenfer_synergie", "icon": "👥", "name": "5er-Synergie",
        "beschreibung": "Siege als kompletter 5er-Stack (alle 5 im selben Team)",
        "stufen": [1, 3, 8, 20, 40],
    },
]


def _werte_je_gruppe(cur, puuids):
    """Ein Aggregat-Query über ALLE Mitglieder zusammen (nicht pro Kopf), plus ein zweites für
    5er-Stack-Siege - dasselbe (match_id, win)-Prinzip wie seiten_gruppen.stack_stats()."""
    if not puuids:
        return {d["id"]: 0 for d in GRUPPEN_TROPHAEEN_DEFINITIONEN}
    cur.execute("""
        SELECT
            COUNT(*) AS vereint,
            COALESCE(SUM(CASE WHEN p.win THEN 1 ELSE 0 END), 0) AS siegeszug,
            COALESCE(SUM(p.penta_kills), 0) AS pentakill_arsenal,
            COUNT(*) FILTER (
                WHERE p.deaths = 0 AND COALESCE(m.duration_seconds, 0) >= %(deathless_dauer)s
                  AND (p.kills + p.assists) >= %(deathless_beteiligung)s
            ) AS makellos,
            COALESCE(SUM(p.turret_takedowns), 0) AS turmsturm
        FROM participants p JOIN matches m ON m.match_id = p.match_id
        WHERE p.puuid = ANY(%(puuids)s);
    """, {
        "puuids": puuids,
        "deathless_dauer": DEATHLESS_MIN_DAUER,
        "deathless_beteiligung": DEATHLESS_MIN_BETEILIGUNG,
    })
    spalten = ["vereint", "siegeszug", "pentakill_arsenal", "makellos", "turmsturm"]
    werte = dict(zip(spalten, cur.fetchone()))

    cur.execute("""
        SELECT win, COUNT(*) FROM participants
        WHERE puuid = ANY(%s)
        GROUP BY match_id, win
        HAVING COUNT(*) = 5;
    """, (puuids,))
    werte["fuenfer_synergie"] = sum(1 for win, _ in cur.fetchall() if win)
    return werte


def trophaeen_fuer_gruppe(cur, puuids):
    """Dieselbe Form wie trophaeen_fuer_spieler() in trophaeen.py - fürs Trophäen-Grid auf der
    Gruppenseite UND für den Punktestand der öffentlichen Rangliste (trophaeen_punkte())."""
    werte = _werte_je_gruppe(cur, puuids)
    ergebnis = []
    for d in GRUPPEN_TROPHAEEN_DEFINITIONEN:
        wert = werte.get(d["id"], 0)
        schwellen = d["stufen"]
        stufe_index = _stufe_fuer_wert(wert, schwellen)
        ist_maximal = stufe_index == len(schwellen) - 1

        if ist_maximal:
            naechste_schwelle = None
            naechste_stufe_label = None
            fortschritt_prozent = 100
        else:
            basis = schwellen[stufe_index] if stufe_index >= 0 else 0
            naechste_schwelle = schwellen[stufe_index + 1]
            naechste_stufe_label = TIER_LABEL[TIER_REIHENFOLGE[stufe_index + 1]]
            spanne = naechste_schwelle - basis
            fortschritt_prozent = round((wert - basis) / spanne * 100) if spanne else 100

        stufe = TIER_REIHENFOLGE[stufe_index] if stufe_index >= 0 else None
        leiter = [
            {
                "stufe": key, "label": TIER_LABEL[key], "schwelle": schwelle,
                "farbe": TIER_FARBE[key], "erreicht": wert >= schwelle,
            }
            for key, schwelle in zip(TIER_REIHENFOLGE, schwellen)
        ]
        ergebnis.append({
            "id": d["id"], "icon": d["icon"], "name": d["name"], "beschreibung": d["beschreibung"],
            "wert": wert, "leiter": leiter, "stufe_index": stufe_index,
            "stufe": stufe, "stufe_label": TIER_LABEL.get(stufe, "Noch keine Stufe"),
            "farbe": TIER_FARBE.get(stufe, FARBE_UNERREICHT),
            "naechste_schwelle": naechste_schwelle, "naechste_stufe_label": naechste_stufe_label,
            "fortschritt_prozent": max(0, min(100, fortschritt_prozent)),
            "ist_maximal": ist_maximal,
        })
    return ergebnis


def trophaeen_punkte(trophaeen):
    """Summe der erreichten Stufen-Indizes + 1 (Bronze=1 ... Diamant=5) über alle Trophäen -
    für die öffentliche Rangliste: belohnt Fortschritt in der Breite statt nur Spielanzahl."""
    return sum(t["stufe_index"] + 1 for t in trophaeen if t["stufe_index"] >= 0)
