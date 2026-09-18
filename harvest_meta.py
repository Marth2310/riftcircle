"""Holt Ranked-Solo-Spiele der EUW-Challenger-Spieler und speichert ALLE 10 Teilnehmer jedes
Matches (nicht nur einen) - als breitere Datenbasis für die Champion-Datenbank (Winrate/Items/
KDA), zusätzlich zu den persönlich getrackten Profilen. Kein Ersatz für eine echte U.GG-Skala
(siehe Gespräch dazu) - ein einmaliger, manuell angestoßener Schnappschuss mit dem persönlichen
Dev-Key, rate-limited auf ca. 1 Anfrage/1,2s (dieselbe Pacing-Regel wie riot_fetch.py).

Speichert bewusst KEINE Timeline (kein Skill-Order aus diesem Harvest) - das würde die
Laufzeit etwa verdoppeln. Skill-Order füllt sich weiterhin nur, wenn Match-Details einzeln
angeschaut werden (siehe dashboard.py match_detail()).

Nutzung: python harvest_meta.py [Anzahl_Spiele_pro_Spieler]
"""
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

from db import get_connection
from riot_fetch import speichere_participant

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

ANZAHL_SPIELE_PRO_SPIELER = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PAUSE_SEKUNDEN = 1.2
REGION_PLATTFORM = "euw1"  # league-v4 läuft über die Plattform-Region (euw1), nicht die
REGION_ROUTING = "europe"  # kontinentale Routing-Region (europe) wie bei match-v5/account-v1


def _get(url, retries=3):
    """GET mit einfachem Retry bei 429 (Rate-Limit) oder 5xx - bei einem ~30-90 Minuten
    laufenden Harvest ist ein gelegentlicher Hänger sonst der Abbruchgrund."""
    for versuch in range(retries):
        resp = requests.get(url, headers=headers)
        if resp.status_code == 429:
            wartezeit = int(resp.headers.get("Retry-After", 5))
            print(f"  Rate-Limit erreicht, warte {wartezeit}s...")
            time.sleep(wartezeit)
            continue
        if resp.status_code >= 500:
            time.sleep(3)
            continue
        return resp
    return resp


def main():
    conn = get_connection()
    cur = conn.cursor()

    print("Hole Challenger-Liste (EUW)...")
    resp = _get(f"https://{REGION_PLATTFORM}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5")
    if resp.status_code != 200:
        print(f"Konnte Challenger-Liste nicht laden: HTTP {resp.status_code} - {resp.text[:200]}")
        return
    spieler_puuids = [e["puuid"] for e in resp.json()["entries"]]
    print(f"{len(spieler_puuids)} Challenger-Spieler gefunden.")

    alle_match_ids = set()
    for i, puuid in enumerate(spieler_puuids, start=1):
        url = (
            f"https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
            f"?queue=420&count={ANZAHL_SPIELE_PRO_SPIELER}"
        )
        resp = _get(url)
        if resp.status_code == 200 and isinstance(resp.json(), list):
            alle_match_ids.update(resp.json())
        if i % 25 == 0 or i == len(spieler_puuids):
            print(f"  Match-IDs: Spieler {i}/{len(spieler_puuids)} - {len(alle_match_ids)} einzigartige Matches bisher")
        time.sleep(PAUSE_SEKUNDEN)

    print(f"{len(alle_match_ids)} einzigartige Match-IDs insgesamt.")

    # Bereits gespeicherte Matches überspringen - der Harvest ist wiederholbar, ohne alte
    # Matches erneut abzufragen (gleiches Prinzip wie die Staleness-Checks in riot_fetch.py).
    cur.execute("SELECT match_id FROM matches WHERE match_id = ANY(%s);", (list(alle_match_ids),))
    bereits_gespeichert = {row[0] for row in cur.fetchall()}
    neue_match_ids = sorted(alle_match_ids - bereits_gespeichert)
    print(f"{len(bereits_gespeichert)} bereits vorhanden, {len(neue_match_ids)} werden neu geholt.")

    gespeicherte_teilnehmer = 0
    for i, match_id in enumerate(neue_match_ids, start=1):
        resp = _get(f"https://{REGION_ROUTING}.api.riotgames.com/lol/match/v5/matches/{match_id}")
        if resp.status_code != 200:
            time.sleep(PAUSE_SEKUNDEN)
            continue
        info = resp.json()["info"]

        # Nur Ranked-Solo-Duo-Matches auf Summoner's Rift verwerten (queueId 420) - der
        # Queue-Filter beim Match-ID-Abruf oben sollte das schon sicherstellen, hier zur
        # Sicherheit nochmal geprüft, falls sich das mal ändert.
        if info.get("queueId") != 420:
            time.sleep(PAUSE_SEKUNDEN)
            continue

        cur.execute("""
            INSERT INTO matches (match_id, played_at, duration_seconds, patch)
            VALUES (%s, to_timestamp(%s), %s, %s)
            ON CONFLICT (match_id) DO NOTHING;
        """, (match_id, info["gameStartTimestamp"] / 1000, info["gameDuration"], info["gameVersion"]))

        for p in info["participants"]:
            # ON CONFLICT DO NOTHING: falls der Teilnehmer zufällig schon ein echtes
            # getracktes Profil ist, NICHT mit einem Platzhalter-Namen überschreiben.
            platzhalter_name = f"Meta-{p['puuid'][:8]}"
            cur.execute("""
                INSERT INTO players (puuid, riot_name, riot_tag, is_meta_sample)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (puuid) DO NOTHING;
            """, (p["puuid"], platzhalter_name, "EUW"))
            speichere_participant(cur, match_id, p, info)
            gespeicherte_teilnehmer += 1

        conn.commit()
        if i % 25 == 0 or i == len(neue_match_ids):
            print(f"  Matches: {i}/{len(neue_match_ids)} gespeichert ({gespeicherte_teilnehmer} Teilnehmer-Zeilen)")
        time.sleep(PAUSE_SEKUNDEN)

    cur.close()
    conn.close()
    print(f"Fertig! {len(neue_match_ids)} neue Matches, {gespeicherte_teilnehmer} Teilnehmer-Zeilen gespeichert.")


if __name__ == "__main__":
    main()
