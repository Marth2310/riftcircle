import os
from dotenv import load_dotenv

from analysis import (
    ANZAHL_MATCHES,
    KATEGORIEN,
    MATCH_QUERY,
    format_wert,
    get_player_ranks,
    match_metrics,
    top_probleme,
)
from benchmarks import normalize_tier
from db import get_connection

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

conn = get_connection()
cur = conn.cursor()


def formatiere_top_problem(key, stats):
    meta = KATEGORIEN[key]
    avg_wert = format_wert(key, stats["avg_wert"])
    avg_richtwert = format_wert(key, stats["avg_richtwert"])

    zeilen = [
        f"{meta['label']}: in {stats['unter_anzahl']}/{stats['total']} Spielen unter Richtwert "
        f"(Schnitt {avg_wert} statt {avg_richtwert})",
        f"  → {meta['tipp']}",
    ]
    return "\n".join(zeilen)


# --- Rang einmal abfragen ---
cur.execute("SELECT puuid FROM players LIMIT 1;")
puuid = cur.fetchone()[0]
ranks = get_player_ranks(puuid, headers)
solo_rang = ranks["solo"]
tier = solo_rang["tier"] if solo_rang else None

if solo_rang:
    print(f"Aktueller Rang (Solo/Duo): {solo_rang['tier']} {solo_rang['rank']}")
    if ranks["flex"]:
        print(f"Aktueller Rang (Flex): {ranks['flex']['tier']} {ranks['flex']['rank']}")
    print()
else:
    print("Kein Solo/Duo-Rang gefunden (unranked oder zu wenig Spiele)\n")
    tier = "GOLD"  # Fallback, falls unranked

tier = normalize_tier(tier)

# --- Letzte N gespielte Matches holen ---
cur.execute(MATCH_QUERY, (puuid, ANZAHL_MATCHES))
rows = cur.fetchall()

if not rows:
    print("Keine Matches in der Datenbank gefunden.")
else:
    alle_vergleiche = []

    print(f"Letzte {len(rows)} Spiele:\n")
    for row in rows:
        werte, vergleich = match_metrics(row, tier)
        alle_vergleiche.append(vergleich)

        ergebnis = "Sieg" if werte["win"] else "Niederlage"
        kp_txt = f"{werte['kill_participation'] * 100:.0f}%" if werte["kill_participation"] is not None else "n/a"
        dmg_txt = f"{werte['damage_share'] * 100:.0f}%" if werte["damage_share"] is not None else "n/a"
        obj_txt = werte["objective_participation"] if werte["objective_participation"] is not None else "n/a"
        solo_txt = werte["solo_kills"] if werte["solo_kills"] is not None else "n/a"
        print(
            f"  {werte['champion']:<12} {werte['role'] or 'ARAM':<8} {ergebnis:<10} "
            f"KDA {werte['kda']:.2f}  CS/Min {werte['cs_per_min']:.1f}  "
            f"Vision/Min {werte['vision_per_min']:.2f}  KP {kp_txt}  Dmg-Share {dmg_txt}  "
            f"Obj-Teiln. {obj_txt}  Solo-Kills {solo_txt}"
        )

    print("\nDeine größten Verbesserungsbereiche (über diese Spiele hinweg):")
    probleme = top_probleme(alle_vergleiche)
    if not probleme:
        print("  → Keine konsistenten Schwächen erkennbar – starke Serie!")
    else:
        for i, (key, stats) in enumerate(probleme, start=1):
            print(f"\n{i}. {formatiere_top_problem(key, stats)}")

cur.close()
conn.close()
