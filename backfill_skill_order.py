"""Füllt skill_order für bereits gespeicherte Matches nach, die noch keine haben (v.a. die
über harvest_meta.py massen-geharvesteten Challenger-Matches - dort wurde die teure Timeline
bewusst übersprungen, siehe dortiger Kommentar). Ein Timeline-Call pro Match liefert die
Skill-Order ALLER 10 Teilnehmer auf einmal (fetch_all_skill_orders), daher deutlich
günstiger als ein Call pro Teilnehmer. Idempotent/resumable: überspringt Matches, die schon
für jeden ihrer gespeicherten Teilnehmer eine skill_order haben.

Nutzung: python backfill_skill_order.py
"""
import json
import os
import time

from dotenv import load_dotenv

from db import get_connection
from riot_assets import REQUEST_TIMEOUT
from riot_timeline import fetch_all_skill_orders

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

PAUSE_SEKUNDEN = 1.2


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT DISTINCT match_id FROM participants WHERE skill_order IS NULL ORDER BY match_id;"
    )
    match_ids = [row[0] for row in cur.fetchall()]
    print(f"{len(match_ids)} Matches mit fehlender Skill-Order.")

    aktualisierte_teilnehmer = 0
    for i, match_id in enumerate(match_ids, start=1):
        ergebnisse = fetch_all_skill_orders(headers, match_id)
        if ergebnisse:
            for puuid, skill_order in ergebnisse.items():
                cur.execute(
                    "UPDATE participants SET skill_order = %s "
                    "WHERE match_id = %s AND puuid = %s AND skill_order IS NULL;",
                    (json.dumps(skill_order), match_id, puuid)
                )
                aktualisierte_teilnehmer += cur.rowcount
            conn.commit()
        if i % 25 == 0 or i == len(match_ids):
            print(f"  {i}/{len(match_ids)} Matches verarbeitet ({aktualisierte_teilnehmer} Teilnehmer aktualisiert)")
        time.sleep(PAUSE_SEKUNDEN)

    cur.close()
    conn.close()
    print(f"Fertig! {aktualisierte_teilnehmer} Teilnehmer-Zeilen mit Skill-Order aktualisiert.")


if __name__ == "__main__":
    main()
