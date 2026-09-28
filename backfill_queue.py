"""Füllt matches.queue_id (Spielmodus: Ranked/Normal/ARAM/...) für Matches nach, die von
vor der Einführung der Spalte stammen.

- Matches aus harvest_meta.py (nur Meta-Stichproben oder alle 10 Teilnehmer gespeichert)
  sind per Konstruktion Ranked Solo/Duo (Queue 420) - die werden ohne API-Call gesetzt.
- Alle anderen (Spiele echter Profile) kosten einen Match-Call; neueste zuerst, damit die
  sichtbaren Match-Historien als erstes vollständig sind. Idempotent/resumable.

Standardmäßig nur für "wichtige" Profile (Gruppenmitglieder, zuletzt gesehene Spieler
angemeldeter Nutzer, verknüpfte Konten) - der Rest sind >13k Matches, die mit dem Dev-Key
Stunden dauern und dessen Rate-Limit der Live-Seite wegnehmen würden.

Nutzung: python backfill_queue.py          (nur wichtige Profile)
         python backfill_queue.py --alle   (alles - erst mit Production Key)
"""
import os
import sys
import time

import requests
from dotenv import load_dotenv

from db import get_connection
from riot_assets import REQUEST_TIMEOUT

load_dotenv()
headers = {"X-Riot-Token": os.environ["RIOT_API_KEY"]}

PAUSE_SEKUNDEN = 1.2
RANKED_SOLO = 420


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE matches m SET queue_id = %s
        WHERE m.queue_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM participants p JOIN players pl ON pl.puuid = p.puuid
              WHERE p.match_id = m.match_id AND NOT pl.is_meta_sample
          );
    """, (RANKED_SOLO,))
    print(f"{cur.rowcount} Meta-Matches direkt auf Ranked Solo/Duo gesetzt.")
    # Alle 10 Teilnehmer gespeichert = von harvest_meta.py geholt (sync_player speichert nur
    # den gesuchten Spieler) - und der Harvest holt ausschließlich Queue 420. Teilnehmer mit
    # echtem Namen machen das Match dabei nicht zu einem "echten" Profil-Match.
    cur.execute("""
        UPDATE matches m SET queue_id = %s
        WHERE m.queue_id IS NULL
          AND (SELECT COUNT(*) FROM participants p WHERE p.match_id = m.match_id) >= 10;
    """, (RANKED_SOLO,))
    print(f"{cur.rowcount} Harvest-Matches (10 Teilnehmer) direkt auf Ranked Solo/Duo gesetzt.")
    conn.commit()

    if "--alle" in sys.argv:
        cur.execute("SELECT match_id FROM matches WHERE queue_id IS NULL ORDER BY played_at DESC;")
    else:
        cur.execute("""
            SELECT m.match_id FROM matches m
            WHERE m.queue_id IS NULL AND EXISTS (
                SELECT 1 FROM participants p
                WHERE p.match_id = m.match_id AND p.puuid IN (
                    SELECT puuid FROM gruppen_mitglieder
                    UNION SELECT puuid FROM nutzer_zuletzt_gesehen
                    UNION SELECT riot_puuid FROM nutzer WHERE riot_puuid IS NOT NULL
                )
            )
            ORDER BY m.played_at DESC;
        """)
    match_ids = [row[0] for row in cur.fetchall()]
    print(f"{len(match_ids)} Matches brauchen einen API-Call.")

    fehler = 0
    for i, match_id in enumerate(match_ids, start=1):
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}"
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            cur.execute("UPDATE matches SET queue_id = %s WHERE match_id = %s;",
                        (resp.json()["info"].get("queueId"), match_id))
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
