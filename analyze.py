import os
import psycopg2
import requests
from dotenv import load_dotenv

from benchmarks import (
    KDA_BENCHMARKS,
    closest_tier,
    closest_tier_flat,
    normalize_tier,
    table_for_role,
)

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

conn = psycopg2.connect(
    host="localhost", port=5432, dbname="lolanalytics",
    user="postgres", password=os.environ["DB_PASSWORD"]
)
cur = conn.cursor()

def get_player_tier(puuid):
    """Holt den aktuellen Solo/Duo-Rang des Spielers direkt über die PUUID."""
    url = f"https://euw1.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    entries = requests.get(url, headers=headers).json()

    for entry in entries:
        if entry["queueType"] == "RANKED_SOLO_5x5":
            return entry["tier"], entry["rank"]
    return None, None

# --- Rang einmal abfragen ---
cur.execute("SELECT puuid FROM players LIMIT 1;")
puuid = cur.fetchone()[0]
tier, rank = get_player_tier(puuid)

if tier:
    print(f"Aktueller Rang: {tier} {rank}\n")
else:
    print("Kein Solo/Duo-Rang gefunden (unranked oder zu wenig Spiele)\n")
    tier = "GOLD"  # Fallback, falls unranked

tier = normalize_tier(tier)

# --- Matches auswerten ---
cur.execute("""
    SELECT p.champion, p.role, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
           m.duration_seconds, p.win
    FROM participants p JOIN matches m ON p.match_id = m.match_id;
""")

for champion, role, kills, deaths, assists, cs, vision, duration, win in cur.fetchall():
    minutes = duration / 60
    kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)
    cs_per_min = cs / minutes
    vision_per_min = vision / minutes
    ergebnis = "Sieg" if win else "Niederlage"

    print(f"{champion} ({role or 'ARAM/unbekannt'}, {ergebnis}):")
    print(f"  KDA: {kda:.2f}  |  CS/Min: {cs_per_min:.1f}  |  Vision/Min: {vision_per_min:.2f}")

    if kda >= KDA_BENCHMARKS[tier]:
        print(f"  ✓ KDA liegt über dem {tier}-Richtwert ({KDA_BENCHMARKS[tier]})")
    else:
        typische_elo_kda = closest_tier_flat(kda, KDA_BENCHMARKS)
        print(f"  ✗ KDA ({kda:.2f}) liegt unter dem {tier}-Richtwert ({KDA_BENCHMARKS[tier]})")
        print(f"    → Dieser Wert entspricht eher dem Durchschnitt in {typische_elo_kda}")

    table = table_for_role(role)
    if table is None:
        print("  → Keine Rollen-Richtwerte verfügbar (vermutlich ARAM).\n")
        continue

    dein_richtwert = table[tier]["vision"]
    typische_elo = closest_tier(vision_per_min, table, "vision")

    if vision_per_min >= dein_richtwert:
        print(f"  ✓ Vision/Min liegt über dem {tier}-Richtwert ({dein_richtwert})")
    else:
        print(f"  ✗ Vision/Min ({vision_per_min:.2f}) liegt unter dem {tier}-Richtwert ({dein_richtwert})")
        print(f"    → Dieser Wert entspricht eher dem Durchschnitt in {typische_elo}")

    if "cs" in table[tier]:
        if cs_per_min >= table[tier]["cs"]:
            print(f"  ✓ CS/Min liegt über dem {tier}-Richtwert ({table[tier]['cs']})")
        else:
            typische_elo_cs = closest_tier(cs_per_min, table, "cs")
            print(f"  ✗ CS/Min ({cs_per_min:.1f}) liegt unter dem {tier}-Richtwert ({table[tier]['cs']})")
            print(f"    → Dieser Wert entspricht eher dem Durchschnitt in {typische_elo_cs}")

    print()

cur.close()
conn.close()