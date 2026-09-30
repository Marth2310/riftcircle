"""Karriere-Trophäen im Xbox/PlayStation-Stil: kumulative Ziele über die GESAMTE getrackte
Historie eines Spielers - im Unterschied zu den Pro-Spiel-Achievements in achievements.py
(dort ein einzelnes besonderes Spiel, hier ein Fortschrittsbalken über die ganze Karriere).
Fünf Stufen wie League selbst, damit sich die Leiter sofort vertraut anfühlt."""

from achievements import DEATHLESS_MIN_BETEILIGUNG, DEATHLESS_MIN_DAUER, VISION_PRO_MIN_SCHWELLE

TIER_REIHENFOLGE = ["bronze", "silber", "gold", "platin", "diamant"]
TIER_LABEL = {"bronze": "Bronze", "silber": "Silber", "gold": "Gold", "platin": "Platin", "diamant": "Diamant"}
# Nach dem Vorbild der League-eigenen Rang-Embleme, damit die Leiter sofort vertraut wirkt.
TIER_FARBE = {
    "bronze": "#8c5a34",
    "silber": "#9aa4ad",
    "gold": "#d4af6a",
    "platin": "#3dd6c6",
    "diamant": "#5cc8f5",
}
FARBE_UNERREICHT = "#3a3560"  # noch keine Stufe geschafft - dezent, blendet nicht

# Jede Definition: id (Spalte in _werte_je_spieler), Anzeige-Name/Icon/Beschreibung, und die 5
# Schwellwerte (Bronze/Silber/Gold/Platin/Diamant). Erste Schätzung, orientiert an der
# Größenordnung der bestehenden Pro-Spiel-Achievement-Kalibrierung (achievements.py) - bei
# Bedarf später gegen die echte Verteilung in der DB nachjustieren.
TROPHAEEN_DEFINITIONEN = [
    {
        "id": "treue", "icon": "🎮", "name": "Treue",
        "beschreibung": "Bei RiftCircle getrackte Spiele",
        "stufen": [25, 100, 300, 750, 1500],
    },
    {
        "id": "kill_serie", "icon": "⚔️", "name": "Kill-Serie",
        "beschreibung": "Kills insgesamt",
        "stufen": [250, 1000, 3000, 7500, 15000],
    },
    {
        "id": "pentakill_sammler", "icon": "🔥", "name": "Pentakill-Sammler",
        "beschreibung": "Pentakills insgesamt",
        "stufen": [1, 3, 8, 20, 40],
    },
    {
        "id": "perfektionist", "icon": "🛡️", "name": "Perfektionist",
        "beschreibung": "Deathless Spiele (kein einziger Tod)",
        "stufen": [3, 10, 30, 75, 150],
    },
    {
        "id": "objective_monopol", "icon": "🐉", "name": "Objective-Monopol",
        "beschreibung": "Jeden Drachen/Herald/Baron geholt, kein einziger für den Gegner",
        "stufen": [2, 8, 20, 50, 100],
    },
    {
        "id": "siegesserie", "icon": "📈", "name": "Siegesserie",
        "beschreibung": "Längste je erreichte Win-Streak",
        "stufen": [5, 8, 12, 16, 20],
    },
    {
        "id": "champion_pool", "icon": "🎭", "name": "Champion-Pool",
        "beschreibung": "Verschiedene Champions mit mindestens einem Sieg",
        "stufen": [10, 25, 50, 80, 120],
    },
    {
        "id": "turmbrecher", "icon": "🏰", "name": "Turmbrecher-Karriere",
        "beschreibung": "Turm-Takedowns insgesamt",
        "stufen": [100, 400, 1000, 2500, 5000],
    },
    {
        "id": "comeback", "icon": "🔄", "name": "Comeback-Siege",
        "beschreibung": "Siege trotz verlorenem Inhibitor",
        "stufen": [2, 6, 15, 35, 70],
    },
    {
        "id": "vision_meister", "icon": "👁️", "name": "Vision-Meister",
        "beschreibung": "Spiele mit klar überdurchschnittlicher Vision Score",
        "stufen": [15, 50, 150, 400, 800],
    },
]


def _werte_je_spieler(cur, puuid):
    """Ein Aggregat-Query für fast alle Trophäen (FILTER-Klauseln statt zehn Einzelabfragen),
    plus ein zweites für die längste Siegesserie - die lässt sich nicht als einfaches Aggregat
    ausdrücken, sondern braucht die chronologische Reihenfolge."""
    cur.execute("""
        SELECT
            COUNT(*) AS treue,
            COALESCE(SUM(p.kills), 0) AS kill_serie,
            COALESCE(SUM(p.penta_kills), 0) AS pentakill_sammler,
            COUNT(*) FILTER (
                WHERE p.deaths = 0 AND COALESCE(m.duration_seconds, 0) >= %(deathless_dauer)s
                  AND (p.kills + p.assists) >= %(deathless_beteiligung)s
            ) AS perfektionist,
            COUNT(*) FILTER (WHERE p.objective_monopol AND p.win) AS objective_monopol,
            COUNT(DISTINCT p.champion) FILTER (WHERE p.win) AS champion_pool,
            COALESCE(SUM(p.turret_takedowns), 0) AS turmbrecher,
            COUNT(*) FILTER (WHERE p.win AND COALESCE(p.inhibitoren_verloren, 0) >= 1) AS comeback,
            COUNT(*) FILTER (
                WHERE COALESCE(m.duration_seconds, 0) > 0
                  AND COALESCE(p.vision_score, 0) / (m.duration_seconds / 60.0) >= %(vision_schwelle)s
            ) AS vision_meister
        FROM participants p JOIN matches m ON m.match_id = p.match_id
        WHERE p.puuid = %(puuid)s;
    """, {
        "puuid": puuid,
        "deathless_dauer": DEATHLESS_MIN_DAUER,
        "deathless_beteiligung": DEATHLESS_MIN_BETEILIGUNG,
        "vision_schwelle": VISION_PRO_MIN_SCHWELLE,
    })
    spalten = [
        "treue", "kill_serie", "pentakill_sammler", "perfektionist", "objective_monopol",
        "champion_pool", "turmbrecher", "comeback", "vision_meister",
    ]
    werte = dict(zip(spalten, cur.fetchone()))

    cur.execute("""
        SELECT p.win FROM participants p JOIN matches m ON m.match_id = p.match_id
        WHERE p.puuid = %s ORDER BY m.played_at;
    """, (puuid,))
    laengste_serie = aktuelle_serie = 0
    for (win,) in cur.fetchall():
        aktuelle_serie = aktuelle_serie + 1 if win else 0
        laengste_serie = max(laengste_serie, aktuelle_serie)
    werte["siegesserie"] = laengste_serie
    return werte


def _stufe_fuer_wert(wert, schwellen):
    """Index der höchsten erreichten Stufe, -1 = noch keine Stufe geschafft."""
    stufe_index = -1
    for i, schwelle in enumerate(schwellen):
        if wert >= schwelle:
            stufe_index = i
    return stufe_index


def trophaeen_fuer_spieler(cur, puuid):
    """Alle Karriere-Trophäen mit aktuellem Stand, Stufe und Fortschritt zur nächsten Stufe -
    fürs Trophäen-Abzeichen-Grid auf dem Profil."""
    werte = _werte_je_spieler(cur, puuid)
    ergebnis = []
    for d in TROPHAEEN_DEFINITIONEN:
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
            "wert": wert, "leiter": leiter,
            "stufe": stufe, "stufe_label": TIER_LABEL.get(stufe, "Noch keine Stufe"),
            "farbe": TIER_FARBE.get(stufe, FARBE_UNERREICHT),
            "naechste_schwelle": naechste_schwelle, "naechste_stufe_label": naechste_stufe_label,
            "fortschritt_prozent": max(0, min(100, fortschritt_prozent)),
            "ist_maximal": ist_maximal,
        })
    return ergebnis
