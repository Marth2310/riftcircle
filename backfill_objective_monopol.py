"""Füllt participants.objective_monopol (Achievement "Objective-Monopol") für Spiele nach,
die von vor der Einführung der Spalte stammen - siehe speichere_participant() in
riot_fetch.py für die Berechnung selbst.

Wie backfill_queue.py: kostet einen Match-Call pro Spiel, deshalb standardmäßig nur für
"wichtige" Profile (Gruppenmitglieder, zuletzt gesehene Spieler, verknüpfte Konten), nicht
für die komplette DB - das wären mit dem Dev-Key mehrere Stunden und würde dessen
Rate-Limit der Live-Seite wegnehmen.

Nutzung: python backfill_objective_monopol.py          (nur wichtige Profile)
         python backfill_objective_monopol.py --alle   (alles - erst mit Production Key)
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

from db import get_connection
from riot_assets import REQUEST_TIMEOUT
from riot_fetch import MONOPOL_OBJEKTE

load_dotenv()
headers = {"X-Riot-Token": os.environ["RIOT_API_KEY"]}

PAUSE_SEKUNDEN = 1.2


def _kills(team, typ):
    return team.get("objectives", {}).get(typ, {}).get("kills", 0)


def main():
    conn = get_connection()
    cur = conn.cursor()

    if "--alle" in sys.argv:
        cur.execute("SELECT DISTINCT match_id FROM participants WHERE objective_monopol IS NULL ORDER BY match_id;")
    else:
        cur.execute("""
            SELECT DISTINCT p.match_id FROM participants p
            WHERE p.objective_monopol IS NULL AND p.puuid IN (
                SELECT puuid FROM gruppen_mitglieder
                UNION SELECT puuid FROM nutzer_zuletzt_gesehen
                UNION SELECT riot_puuid FROM nutzer WHERE riot_puuid IS NOT NULL
            );
        """)
    match_ids = [row[0] for row in cur.fetchall()]
    print(f"{len(match_ids)} Matches brauchen einen API-Call.")

    fehler = 0
    for i, match_id in enumerate(match_ids, start=1):
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}"
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            info = resp.json()["info"]
            teams = {t.get("teamId"): t for t in info.get("teams", [])}
            for p in info["participants"]:
                eigenes_team = teams.get(p["teamId"])
                gegner_team = next((t for tid, t in teams.items() if tid != p["teamId"]), None)
                monopol = None
                if eigenes_team and gegner_team:
                    monopol = all(
                        _kills(eigenes_team, typ) >= 1 and _kills(gegner_team, typ) == 0
                        for typ in MONOPOL_OBJEKTE
                    )
                cur.execute(
                    "UPDATE participants SET objective_monopol = %s WHERE match_id = %s AND puuid = %s;",
                    (monopol, match_id, p["puuid"])
                )
            conn.commit()
        elif resp.status_code in (401, 403):
            print("API-Key ungültig/abgelaufen - Abbruch.")
            break
        else:
            fehler += 1
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 10)))
        if i % 50 == 0:
            print(f"  {i}/{len(match_ids)}")
        time.sleep(PAUSE_SEKUNDEN)

    print(f"Fertig ({fehler} Fehler).")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
