"""Erkennt besondere Leistungen in einem einzelnen Match-Ergebnis - für den Gruppen-Feed
("Rivalität & Freunde"-Gefühl: sehen, wenn jemand aus der Gruppe was Cooles gerissen hat).
Bewusst klein gehalten und leicht erweiterbar (ACHIEVEMENT_DEFINITIONEN) - reine Fakten aus
den Riot-Daten, keine Elo-Vergleiche (die liefert schon die Profil-Seite).

Drei Klassen, je seltener desto wertvoller:
- "legendaer" (rot) - die absolute Ausnahme-Leistung in einem Spiel
- "episch" (grün)   - klar überdurchschnittlich, aber öfter mal drin
- "achievement" (gold) - die bisherige Basis-Klasse, schöne Momente ohne Seltenheitsanspruch

Die Priorität (100er-Bänder je Klasse) sorgt dafür, dass bestes_achievement() bei mehreren
Treffern in einem Match automatisch die höchste Klasse zeigt."""

# Alle Schwellwerte unten sind gegen die echte Verteilung in der DB kalibriert (Stand
# 2026-09), Ziel: "legendaer" trifft auf ~1-3% der Spiele zu, "episch" auf ~8-15%, statt
# gefühlt in jedem dritten Spiel zu triggern.

# Ab so vielen Sekunden zählt ein Spiel für "Deathless" als vollständig gespielt (kein
# frühzeitiger Remake mit Zufalls-0-Toden); der Kill+Assist-Mindestwert verhindert, dass ein
# rein passives 0/0/2 als "Deathless" durchgeht
DEATHLESS_MIN_DAUER = 900
DEATHLESS_MIN_BETEILIGUNG = 3
KDA_LEGENDAER_SCHWELLE = 15.0  # ~2.5% der Spiele - "10.0" (ursprüngliche Idee) lag bei ~5.9%
SOLO_KILLS_LEGENDAER = 7  # ~0.9% der Spiele - "5" lag bei ~3.7%, zu häufig für die seltenste Klasse
CS_PRO_MIN_SCHWELLE = 10.0  # ~4.3% der Spiele - "8.0" lag bei ~36%, viel zu häufig
VISION_PRO_MIN_SCHWELLE = 4.0  # ~1.2% der Spiele
TURMBRECHER_SCHWELLE = 7  # ~2.7% der Spiele
DAMAGE_DOMINANZ_ANTEIL = 0.30  # zusätzlich zu Platz 1: mindestens 30% des Team-Schadens, sonst zu häufig
LANE_DOMINANZ_GOLD_DIFF = 3000  # ~14% der Spiele - "1500" lag bei ~26%

ACHIEVEMENT_DEFINITIONEN = [
    # --- Legendär (rot) - die seltensten, spektakulärsten Momente -------------------------
    {
        "id": "penta",
        "icon": "🔥",
        "tier": "legendaer",
        "prioritaet": 290,
        "pruefung": lambda r: r["penta_kills"] >= 1,
        "text": lambda r: f"PENTAKILL mit {r['champion']}!",
    },
    {
        "id": "deathless",
        "icon": "🛡️",
        "tier": "legendaer",
        "prioritaet": 280,
        "pruefung": lambda r: (
            r["deaths"] == 0 and (r.get("duration_seconds") or 0) >= DEATHLESS_MIN_DAUER
            and (r["kills"] + r["assists"]) >= DEATHLESS_MIN_BETEILIGUNG
        ),
        "text": lambda r: f"Deathless - {r['kills']}/0/{r['assists']} über das komplette Spiel mit {r['champion']}",
    },
    {
        "id": "kda_extrem",
        "icon": "👑",
        "tier": "legendaer",
        "prioritaet": 270,
        "pruefung": lambda r: r["deaths"] >= 1 and (r["kills"] + r["assists"]) / r["deaths"] >= KDA_LEGENDAER_SCHWELLE,
        "text": lambda r: f"{(r['kills'] + r['assists']) / r['deaths']:.1f}:1 KDA mit {r['champion']} "
                          f"({r['kills']}/{r['deaths']}/{r['assists']})",
    },
    {
        "id": "einmann_armee",
        "icon": "⚔️",
        "tier": "legendaer",
        "prioritaet": 260,
        "pruefung": lambda r: r["solo_kills"] >= SOLO_KILLS_LEGENDAER,
        "text": lambda r: f"{r['solo_kills']} Solo-Kills mit {r['champion']} - Einmann-Armee",
    },
    {
        "id": "vollkommene_kontrolle",
        "icon": "💫",
        "tier": "legendaer",
        "prioritaet": 250,
        "pruefung": lambda r: r["win"] and (r.get("damage_share") or 0) >= 0.50,
        "text": lambda r: f"{r['damage_share'] * 100:.0f}% des Team-Schadens mit {r['champion']} - vollkommene Kontrolle",
    },

    # --- Episch (grün) - klar überdurchschnittlich ---------------------------------------
    {
        "id": "quadra",
        "icon": "⚡",
        "tier": "episch",
        "prioritaet": 190,
        "pruefung": lambda r: r["penta_kills"] == 0 and r["quadra_kills"] >= 1,
        "text": lambda r: f"Quadra Kill mit {r['champion']}",
    },
    {
        "id": "objective_monopol",
        "icon": "🏆",
        "tier": "episch",
        "prioritaet": 180,
        "pruefung": lambda r: r.get("objective_monopol") and r["win"],
        "text": lambda r: "Objective-Monopol - jeden Drachen, Herald und Baron geholt, "
                          f"kein einziger für den Gegner, mit {r['champion']}",
    },
    {
        "id": "most_damage",
        "icon": "💥",
        "tier": "episch",
        "prioritaet": 170,
        "pruefung": lambda r: r["damage_rank"] == 1 and (r.get("damage_share") or 0) >= DAMAGE_DOMINANZ_ANTEIL,
        "text": lambda r: (
            f"Höchster Schaden im gesamten Spiel (Platz 1/10, {r['damage_dealt']:,}".replace(",", ".")
            + f") & Sieg mit {r['champion']}"
        ) if r["win"] else (
            "Trotz Niederlage: höchster Schaden im gesamten Spiel (Platz 1/10, "
            + f"{r['damage_dealt']:,}".replace(",", ".") + f") mit {r['champion']}"
        ),
    },
    {
        "id": "turmbrecher",
        "icon": "🏰",
        "tier": "episch",
        "prioritaet": 160,
        "pruefung": lambda r: (r.get("turret_takedowns") or 0) >= TURMBRECHER_SCHWELLE,
        "text": lambda r: f"{r['turret_takedowns']} Türme mitgenommen mit {r['champion']} - Turmbrecher",
    },
    {
        "id": "above_avg_cs",
        "icon": "🌾",
        "tier": "episch",
        "prioritaet": 155,
        "pruefung": lambda r: bool(r.get("duration_seconds")) and r["cs"] / (r["duration_seconds"] / 60) >= CS_PRO_MIN_SCHWELLE,
        "text": lambda r: f"{r['cs'] / (r['duration_seconds'] / 60):.1f} CS/Min mit {r['champion']} - klar überdurchschnittlich",
    },
    {
        "id": "vision_meister",
        "icon": "👁️",
        "tier": "episch",
        "prioritaet": 150,
        "pruefung": lambda r: bool(r.get("duration_seconds")) and (r.get("vision_score") or 0) / (r["duration_seconds"] / 60) >= VISION_PRO_MIN_SCHWELLE,
        "text": lambda r: f"Vision-Score {r['vision_score']} ({r['vision_score'] / (r['duration_seconds'] / 60):.1f}/Min) mit {r['champion']}",
    },

    # --- Achievement (gold) - die Basis-Klasse, schöne Momente ----------------------------
    {
        "id": "comeback",
        "icon": "🔄",
        "tier": "achievement",
        "prioritaet": 90,
        "pruefung": lambda r: r["win"] and (r.get("inhibitoren_verloren") or 0) >= 1,
        "text": lambda r: f"Comeback-Sieg mit {r['champion']} - trotz verlorenem Inhibitor",
    },
    {
        "id": "lane_dominanz",
        "icon": "💰",
        "tier": "achievement",
        "prioritaet": 80,
        "pruefung": lambda r: r["win"] and (r.get("gold_diff") or 0) >= LANE_DOMINANZ_GOLD_DIFF,
        "text": lambda r: f"{r['gold_diff']:,}".replace(",", ".") + f" Gold Vorsprung auf den Lane-Gegner mit {r['champion']} - klare Lane-Dominanz",
    },
    {
        "id": "triple",
        "icon": "🎯",
        "tier": "achievement",
        "prioritaet": 70,
        "pruefung": lambda r: r["penta_kills"] == 0 and r["quadra_kills"] == 0 and r["triple_kills"] >= 1,
        "text": lambda r: f"Triple Kill mit {r['champion']}",
    },
    {
        "id": "solo_kills",
        "icon": "🗡️",
        "tier": "achievement",
        "prioritaet": 60,
        "pruefung": lambda r: r["solo_kills"] >= 3,
        "text": lambda r: f"{r['solo_kills']} Solo-Kills mit {r['champion']}",
    },
    {
        "id": "doppelkill",
        "icon": "✌️",
        "tier": "achievement",
        "prioritaet": 50,
        "pruefung": lambda r: r["penta_kills"] == 0 and r["quadra_kills"] == 0 and r["triple_kills"] == 0 and r["double_kills"] >= 1,
        "text": lambda r: f"Doppelkill mit {r['champion']}",
    },
    {
        "id": "objective_steal",
        "icon": "🐲",
        "tier": "achievement",
        "prioritaet": 40,
        "pruefung": lambda r: r["objectives_stolen"] >= 1,
        "text": lambda r: f"Objective gestohlen mit {r['champion']}",
    },
    {
        # "erstes_mal" berechnet baue_gruppen_feed(): erstes gespeichertes Spiel mit diesem
        # Champion bei einem Spieler, der schon genug Historie hat
        "id": "neuer_champion",
        "icon": "🆕",
        "tier": "achievement",
        "prioritaet": 25,
        "pruefung": lambda r: r.get("erstes_mal", False),
        "text": lambda r: f"Zum ersten Mal {r['champion']} gespielt"
                          + (" - und direkt gewonnen" if r["win"] else ""),
    },
    {
        # "siegesserie" berechnet baue_gruppen_feed() aus der Spielhistorie - nur an runden
        # Marken, sonst käme bei einer 8er-Serie in jedem Spiel ab dem 5. ein Achievement
        "id": "siegesserie",
        "icon": "📈",
        "tier": "achievement",
        "prioritaet": 20,
        "pruefung": lambda r: r.get("siegesserie") in (5, 10, 15, 20),
        "text": lambda r: f"{r['siegesserie']} Siege in Folge - zuletzt mit {r['champion']}",
    },
]

TIER_LABEL = {"legendaer": "Legendär", "episch": "Episch", "achievement": "Achievement"}


def erkenne_achievements(row):
    """Alle Achievements, die EIN Match-Ergebnis erfüllt - sortiert nach Priorität (Pentakill
    schlägt z.B. immer Damage-König, und Legendär schlägt immer Episch/Achievement dank der
    Prioritäts-Bänder). Erwartet ein Dict mit den relevanten participants-Feldern."""
    treffer = [d for d in ACHIEVEMENT_DEFINITIONEN if d["pruefung"](row)]
    treffer.sort(key=lambda d: d["prioritaet"], reverse=True)
    return [{"icon": d["icon"], "text": d["text"](row), "tier": d["tier"], "tier_label": TIER_LABEL[d["tier"]]} for d in treffer]


def bestes_achievement(row):
    """Nur die EINE auffälligste Leistung eines Matches (für kompakte Feed-Einträge) - None
    falls nichts Besonderes passiert ist."""
    treffer = erkenne_achievements(row)
    return treffer[0] if treffer else None
