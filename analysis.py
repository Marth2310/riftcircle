import math

import requests

from riot_assets import REQUEST_TIMEOUT
from benchmarks import (
    DAMAGE_SHARE_MIN_NON_SUPPORT,
    KDA_BENCHMARKS,
    KILL_PARTICIPATION_BENCHMARKS,
    OBJECTIVE_PARTICIPATION_MIN,
    table_for_role,
)

# Musteranalyse statt Einzelspiel-Tipps: erst über mehrere Spiele lässt sich ein
# echter Trend (z.B. "CS ist konstant zu niedrig") von einem Ausreißer unterscheiden.
ANZAHL_MATCHES = 20

MATCH_QUERY = """
    SELECT p.match_id, p.champion, p.role, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
           m.duration_seconds, p.win, p.kill_participation, p.damage_share,
           p.turret_takedowns, p.objectives_stolen, p.solo_kills,
           p.items, p.champ_level, m.played_at, p.damage_rank, p.gold_earned, p.gold_diff,
           p.damage_dealt, p.perks
    FROM participants p JOIN matches m ON p.match_id = m.match_id
    WHERE p.puuid = %s
    ORDER BY m.played_at DESC
    LIMIT %s;
"""


def get_player_tier(puuid, headers):
    """Holt den aktuellen Solo/Duo-Rang des Spielers direkt über die PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT).json()

    for entry in entries:
        if entry["queueType"] == "RANKED_SOLO_5x5":
            return entry["tier"], entry["rank"]
    return None, None


def match_metrics(row, tier):
    """Berechnet Kennzahlen + Elo-Vergleich (Wert, Richtwert, unter Richtwert?) für ein Match."""
    (match_id, champion, role, kills, deaths, assists, cs, vision, duration, win,
     kill_participation, damage_share, turret_takedowns, objectives_stolen, solo_kills,
     items, champ_level, played_at, damage_rank, gold_earned, gold_diff,
     damage_dealt, perks) = row

    minutes = duration / 60
    kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)
    cs_per_min = cs / minutes
    vision_per_min = vision / minutes
    table = table_for_role(role)

    objective_participation = None
    if turret_takedowns is not None and objectives_stolen is not None:
        objective_participation = turret_takedowns + objectives_stolen

    werte = {
        "match_id": match_id,
        "champion": champion, "role": role, "win": win,
        "kills": kills, "deaths": deaths, "assists": assists,
        "kda": kda, "cs_per_min": cs_per_min, "vision_per_min": vision_per_min,
        "kill_participation": kill_participation, "damage_share": damage_share,
        "objective_participation": objective_participation, "solo_kills": solo_kills,
        "items": items or [0] * 7, "champ_level": champ_level, "played_at": played_at,
        "duration_seconds": duration,
        "damage_rank": damage_rank, "gold_earned": gold_earned, "gold_diff": gold_diff,
        "damage_dealt": damage_dealt, "perks": perks,
    }

    vergleich = {"kda": (kda, KDA_BENCHMARKS[tier])}

    if kill_participation is not None:
        vergleich["kill_participation"] = (kill_participation, KILL_PARTICIPATION_BENCHMARKS[tier])

    if table is not None:
        vergleich["vision_per_min"] = (vision_per_min, table[tier]["vision"])
        if "cs" in table[tier]:
            vergleich["cs_per_min"] = (cs_per_min, table[tier]["cs"])
        if role != "UTILITY" and damage_share is not None:
            vergleich["damage_share"] = (damage_share, DAMAGE_SHARE_MIN_NON_SUPPORT)
        # Keine Dragons/Baron/Herald in ARAM, daher nur auf Summoner's Rift sinnvoll
        if objective_participation is not None:
            vergleich["objective_participation"] = (objective_participation, OBJECTIVE_PARTICIPATION_MIN)

    return werte, vergleich


KATEGORIEN = {
    "kda": {
        "label": "KDA",
        "einheit": "",
        "nachkomma": 2,
        "tipp": "Achte auf sicherere Positionierung in Teamfights und riskiere weniger unnötige "
                "Trades, wenn viele Deaths der Grund sind. Ist stattdessen die Kill-Beteiligung "
                "niedrig, such aktiver den Teamfight statt Farm-fokussiert zu spielen.",
    },
    "cs_per_min": {
        "label": "CS/Min",
        "einheit": "",
        "nachkomma": 1,
        "tipp": "Übe Last-Hitting gezielt im Trainingsmodus (Ziel: 10 CS/Min ohne Tower-Aggro) "
                "und minimiere verlorene CS durch Roams/Recalls zur falschen Zeit.",
    },
    "vision_per_min": {
        "label": "Vision/Min",
        "einheit": "",
        "nachkomma": 2,
        "tipp": "Kaufe konsequent Control Wards nach jedem Recall und setze sie an Objective-"
                "relevanten Punkten (Drache/Baron-Büsche), statt nur die Trinket-Ward zu nutzen.",
    },
    "kill_participation": {
        "label": "Kill-Participation",
        "einheit": "%",
        "nachkomma": 0,
        "skala": 100,
        "tipp": "Du verpasst überdurchschnittlich viele Teamfights/Objectives. Reagiere schneller "
                "auf Pings und halte nach verlorenen Lanes engeren Kontakt zum Team, statt isoliert "
                "weiterzufarmen.",
    },
    "damage_share": {
        "label": "Damage-Share",
        "einheit": "%",
        "nachkomma": 0,
        "skala": 100,
        "tipp": "Dein Schadensanteil am Team ist niedrig für eine Damage-Rolle. Prüfe deine "
                "Item-Reihenfolge auf Schadensfokus und suche in Teamfights aktiver nach sicheren "
                "Schadensfenstern, statt nur zu überleben.",
    },
    "objective_participation": {
        "label": "Objective-Teilnahme",
        "einheit": "",
        "nachkomma": 1,
        "tipp": "Du bist an Turm-Takedowns und Objectives (Drache/Herald/Baron) kaum beteiligt. "
                "Reagiere schneller auf Objective-Calls, pushe deine Lane rechtzeitig frei und "
                "rotiere vor Spawns dorthin, statt isoliert weiterzufarmen.",
    },
}


def format_wert(key, wert):
    """Formatiert einen Rohwert passend zur Kategorie (Skala, Nachkommastellen, Einheit)."""
    meta = KATEGORIEN[key]
    skala = meta.get("skala", 1)
    n = meta["nachkomma"]
    return f"{wert * skala:.{n}f}{meta['einheit']}"


def aggregiere(alle_vergleiche):
    """Fasst die Einzelspiel-Vergleiche pro Kategorie zusammen: wie oft/weit unter Richtwert."""
    ergebnis = {}
    for key in KATEGORIEN:
        werte = [v[key] for v in alle_vergleiche if key in v]
        if not werte:
            continue
        total = len(werte)
        unter = [wert for wert, richtwert in werte if wert < richtwert]
        avg_wert = sum(wert for wert, _ in werte) / total
        avg_richtwert = sum(richtwert for _, richtwert in werte) / total
        ergebnis[key] = {
            "total": total,
            "unter_anzahl": len(unter),
            "unter_quote": len(unter) / total,
            "ueber_quote": 1 - (len(unter) / total),
            "avg_wert": avg_wert,
            "avg_richtwert": avg_richtwert,
            "rel_defizit": max(0.0, (avg_richtwert - avg_wert) / avg_richtwert) if avg_richtwert else 0,
            "rel_ueberschuss": max(0.0, (avg_wert - avg_richtwert) / avg_richtwert) if avg_richtwert else 0,
        }
    return ergebnis


def vergleichssatz(label, avg_wert, avg_richtwert, tier):
    """Formuliert den Elo-Vergleich als Satz, z.B. 'Dein Vision Score liegt im Schnitt
    12% über dem Diamond-Durchschnitt.' - direkt nutzbar als Kommentar/Tipp-Text."""
    if not avg_richtwert:
        return f"Dein {label} lässt sich aktuell nicht mit einem {tier.title()}-Richtwert vergleichen."
    diff_pct = round((avg_wert - avg_richtwert) / avg_richtwert * 100)
    if diff_pct >= 0:
        return f"Dein {label} liegt im Schnitt {diff_pct}% über dem {tier.title()}-Durchschnitt."
    return f"Dein {label} liegt im Schnitt {abs(diff_pct)}% unter dem {tier.title()}-Durchschnitt."


# Feste Auswahl für die Ring-Diagramme je Match-Karte (Objective-Teilnahme/Damage-Share
# werden dort stattdessen als kompakte Textzeile gezeigt, sonst wird die Karte zu voll)
RING_KATEGORIEN = ["kda", "cs_per_min", "vision_per_min", "kill_participation"]

# Notenskala S+ bis D-: Schwelle = Mittelwert der einzelnen Elo-Quotienten (wert/richtwert)
# über die 4 Ring-Kategorien. 1.0 = genau Elo-Durchschnitt, entspricht "A".
NOTEN_SKALA = [
    (1.40, "S+"), (1.25, "S"), (1.15, "S-"),
    (1.05, "A+"), (0.95, "A"), (0.85, "A-"),
    (0.75, "B+"), (0.65, "B"), (0.55, "B-"),
    (0.45, "C+"), (0.35, "C"), (0.25, "C-"),
    (0.15, "D+"), (0.05, "D"),
]


def berechne_note(vergleich):
    """Leitet aus den 4 Ring-Kategorien eine Gesamt-Note S+ bis D- ab: Mittelwert der
    einzelnen Elo-Quotienten (wert/richtwert), pro Kategorie auf 200% gedeckelt, damit ein
    einzelner Ausreißer (z.B. ein Pentakill-KDA) nicht allein die Note verzerrt."""
    quotienten = []
    for key in RING_KATEGORIEN:
        if key not in vergleich:
            continue
        wert, richtwert = vergleich[key]
        if richtwert:
            quotienten.append(min(2.0, wert / richtwert))
    if not quotienten:
        return "?"
    schnitt = sum(quotienten) / len(quotienten)
    for schwelle, note in NOTEN_SKALA:
        if schnitt >= schwelle:
            return note
    return "D-"


def ringe_fuer_match(vergleich):
    """Baut die Ring-Diagramm-Daten (% vom Richtwert je Kategorie) für eine Match-Karte,
    inkl. Position auf einem Halbkreis-Bogen unterhalb des Champion-Portraits."""
    ringe = []
    for key in RING_KATEGORIEN:
        if key not in vergleich:
            continue
        wert, richtwert = vergleich[key]
        pct = min(100, round(wert / richtwert * 100)) if richtwert else 0
        ringe.append({
            "label": KATEGORIEN[key]["label"],
            "text": format_wert(key, wert),
            "pct": pct,
            "gut": wert >= richtwert,
        })

    # Von oben nach unten anordnen (nicht links-rechts): die Ringe kaskadieren vertikal
    # unter dem Champion-Portrait, mit einer sanften seitlichen Wölbung in der Mitte.
    # Der vertikale Schritt allein ist schon >= Ringdurchmesser + Abstand - dadurch kann
    # der seitliche Wobble die Ringe NIE überlappen lassen (sqrt(x²+y²) >= y immer).
    n = len(ringe)
    vertikaler_schritt = 60  # > Ringdurchmesser (46px) + Sicherheitsabstand
    seitlicher_wobble = 16
    for i, ring in enumerate(ringe):
        t = i / (n - 1) if n > 1 else 0.5
        ring["bogen_x"] = round(seitlicher_wobble * math.sin(math.pi * t), 1)
        ring["bogen_y"] = round(vertikaler_schritt * i, 1)

    return ringe


def top_probleme(alle_vergleiche, limit=3):
    """Liefert die (bis zu) `limit` konsistentesten Schwächen, priorisiert nach Häufigkeit,
    dann nach relativer Abweichung vom Richtwert."""
    aggregiert = aggregiere(alle_vergleiche)
    prioritaet = sorted(
        (item for item in aggregiert.items() if item[1]["unter_anzahl"] > 0),
        key=lambda item: (item[1]["unter_quote"], item[1]["rel_defizit"]),
        reverse=True,
    )
    return prioritaet[:limit]


def top_staerken(alle_vergleiche, limit=3):
    """Gegenstück zu top_probleme: die (bis zu) `limit` konsistentesten Stärken - Kategorien,
    die am häufigsten über dem Elo-Richtwert liegen, priorisiert nach Häufigkeit, dann nach
    relativem Überschuss."""
    aggregiert = aggregiere(alle_vergleiche)
    prioritaet = sorted(
        (item for item in aggregiert.items() if item[1]["ueber_quote"] > 0.5),
        key=lambda item: (item[1]["ueber_quote"], item[1]["rel_ueberschuss"]),
        reverse=True,
    )
    return prioritaet[:limit]
