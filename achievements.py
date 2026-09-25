"""Erkennt besondere Leistungen in einem einzelnen Match-Ergebnis - für den Gruppen-Feed
("Rivalität & Freunde"-Gefühl: sehen, wenn jemand aus der Gruppe was Cooles gerissen hat).
Bewusst klein gehalten und leicht erweiterbar (ACHIEVEMENT_DEFINITIONEN) - reine Fakten aus
den Riot-Daten, keine Elo-Vergleiche (die liefert schon die Profil-Seite)."""

ACHIEVEMENT_DEFINITIONEN = [
    {
        "id": "penta",
        "icon": "🔥",
        "prioritaet": 100,
        "pruefung": lambda r: r["penta_kills"] >= 1,
        "text": lambda r: f"PENTAKILL mit {r['champion']}!",
    },
    {
        "id": "quadra",
        "icon": "⚡",
        "prioritaet": 90,
        "pruefung": lambda r: r["penta_kills"] == 0 and r["quadra_kills"] >= 1,
        "text": lambda r: f"Quadra Kill mit {r['champion']}",
    },
    {
        # "siegesserie" berechnet baue_gruppen_feed() aus der Spielhistorie - nur an runden
        # Marken, sonst käme bei einer 8er-Serie in jedem Spiel ab dem 5. ein Achievement
        "id": "siegesserie",
        "icon": "📈",
        "prioritaet": 80,
        "pruefung": lambda r: r.get("siegesserie") in (5, 10, 15, 20),
        "text": lambda r: f"{r['siegesserie']} Siege in Folge - zuletzt mit {r['champion']}",
    },
    {
        "id": "comeback",
        "icon": "🔄",
        "prioritaet": 65,
        "pruefung": lambda r: r["win"] and (r.get("inhibitoren_verloren") or 0) >= 1,
        "text": lambda r: f"Comeback-Sieg mit {r['champion']} - trotz verlorenem Inhibitor",
    },
    {
        "id": "perfect_kda",
        "icon": "🛡️",
        "prioritaet": 70,
        "pruefung": lambda r: r["deaths"] == 0 and r["kills"] >= 5,
        "text": lambda r: f"Perfektes Spiel - {r['kills']}/0/{r['assists']} mit {r['champion']}",
    },
    {
        "id": "triple",
        "icon": "🎯",
        "prioritaet": 60,
        "pruefung": lambda r: r["penta_kills"] == 0 and r["quadra_kills"] == 0 and r["triple_kills"] >= 1,
        "text": lambda r: f"Triple Kill mit {r['champion']}",
    },
    {
        "id": "objective_steal",
        "icon": "🐲",
        "prioritaet": 55,
        "pruefung": lambda r: r["objectives_stolen"] >= 1,
        "text": lambda r: f"Objective gestohlen mit {r['champion']}",
    },
    {
        "id": "damage_koenig",
        "icon": "💥",
        "prioritaet": 40,
        "pruefung": lambda r: r["damage_rank"] == 1 and r["win"],
        "text": lambda r: f"{r['damage_dealt']:,}".replace(",", ".") + f" Schaden (Platz 1) & Sieg mit {r['champion']}",
    },
    {
        "id": "solo_kills",
        "icon": "⚔️",
        "prioritaet": 35,
        "pruefung": lambda r: r["solo_kills"] >= 3,
        "text": lambda r: f"{r['solo_kills']} Solo-Kills mit {r['champion']}",
    },
    {
        "id": "carry",
        "icon": "💪",
        "prioritaet": 30,
        "pruefung": lambda r: r["damage_rank"] == 1 and not r["win"],
        "text": lambda r: "Carry-Versuch: " + f"{r['damage_dealt']:,}".replace(",", ".")
                          + f" Schaden (Platz 1) mit {r['champion']} - trotz Niederlage",
    },
    {
        # "erstes_mal" berechnet baue_gruppen_feed(): erstes gespeichertes Spiel mit diesem
        # Champion bei einem Spieler, der schon genug Historie hat
        "id": "neuer_champion",
        "icon": "🆕",
        "prioritaet": 25,
        "pruefung": lambda r: r.get("erstes_mal", False),
        "text": lambda r: f"Zum ersten Mal {r['champion']} gespielt"
                          + (" - und direkt gewonnen" if r["win"] else ""),
    },
]


def erkenne_achievements(row):
    """Alle Achievements, die EIN Match-Ergebnis erfüllt - sortiert nach Priorität (Pentakill
    schlägt z.B. immer Damage-König). Erwartet ein Dict mit den relevanten participants-Feldern."""
    treffer = [d for d in ACHIEVEMENT_DEFINITIONEN if d["pruefung"](row)]
    treffer.sort(key=lambda d: d["prioritaet"], reverse=True)
    return [{"icon": d["icon"], "text": d["text"](row)} for d in treffer]


def bestes_achievement(row):
    """Nur die EINE auffälligste Leistung eines Matches (für kompakte Feed-Einträge) - None
    falls nichts Besonderes passiert ist."""
    treffer = erkenne_achievements(row)
    return treffer[0] if treffer else None
