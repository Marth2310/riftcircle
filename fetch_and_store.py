import os
import sys

from dotenv import load_dotenv

from db import get_connection
from riot_fetch import SummonerNotFound, sync_player

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

# Nutzung: python fetch_and_store.py [RiotName RiotTag]
# Ohne Argumente werden RIOT_NAME/RIOT_TAG aus der .env verwendet.
RIOT_NAME = sys.argv[1] if len(sys.argv) > 1 else os.environ["RIOT_NAME"]
RIOT_TAG = sys.argv[2] if len(sys.argv) > 2 else os.environ["RIOT_TAG"]

conn = get_connection()
cur = conn.cursor()

try:
    puuid, anzahl_neu = sync_player(cur, conn, headers, RIOT_NAME, RIOT_TAG)
    print(f"PUUID gefunden: {puuid}")
    print(f"{anzahl_neu} neue Spiele gespeichert.")
except SummonerNotFound as e:
    print(e)

cur.close()
conn.close()
print("Fertig!")
