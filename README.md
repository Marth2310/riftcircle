# LoL Analytics

Ein persönliches League-of-Legends-Stats-Dashboard: holt Matchdaten über die Riot-API,
speichert sie in Postgres und zeigt sie in einem lokalen Flask-Dashboard an — mit
Elo-Richtwert-Vergleichen, Musteranalyse über mehrere Spiele, Postgame-Details
(Items, Runen, Death-Map, Team-Aufstellung, Lane-Vergleich) und mehr.

## Setup

1. Postgres-Datenbank `lolanalytics` anlegen (lokal, Standardport 5432).
2. Dependencies installieren:
   ```
   python -m venv venv
   source venv/bin/activate
   pip install psycopg2-binary requests python-dotenv flask
   ```
3. `.env.example` nach `.env` kopieren und ausfüllen:
   - `DB_PASSWORD` – dein Postgres-Passwort
   - `RIOT_API_KEY` – von https://developer.riotgames.com/ (Development-Keys laufen alle
     24h ab und müssen neu generiert werden)
   - `RIOT_NAME` / `RIOT_TAG` – dein Riot-ID (z.B. `Faker` / `KR1`), Standard-Account für
     `fetch_and_store.py` ohne Argumente
4. Tabellen anlegen:
   ```
   python create_tables.py
   ```
5. Erste Spiele holen:
   ```
   python fetch_and_store.py
   ```
6. Dashboard starten:
   ```
   python dashboard.py
   ```
   Dann `http://127.0.0.1:5050` im Browser öffnen.

## Hinweise

- Nur EUW/Europe-Accounts werden unterstützt (Region fest in den API-Calls verdrahtet).
- Im Dashboard kann über das Suchfeld auch nach anderen Profilen gesucht werden
  (`Name#Tag`) – die Daten werden dann automatisch nachgeladen.
- `tips.py` gibt dieselbe Musteranalyse als reine Kommandozeilen-Ausgabe aus.
