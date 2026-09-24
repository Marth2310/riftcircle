from dotenv import load_dotenv

from db import get_connection

load_dotenv()  # liest die .env-Datei ein

conn = get_connection()
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS players (
    puuid TEXT PRIMARY KEY,
    riot_name TEXT NOT NULL,
    riot_tag TEXT NOT NULL,
    discord_id TEXT,
    is_meta_sample BOOLEAN DEFAULT FALSE
);
""")
# Meta-Sample-Accounts (z.B. Challenger/Grandmaster-Harvest für die Champion-Datenbank, siehe
# harvest_meta.py) sind keine echten getrackten Profile und sollen nirgends im "bekannte
# Spieler"-Schnellzugriff auftauchen.
cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS is_meta_sample BOOLEAN DEFAULT FALSE;")

cur.execute("""
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    played_at TIMESTAMP,
    duration_seconds INTEGER,
    patch TEXT,
    team_lineup JSONB,
    gold_timeline JSONB,
    timeline_extra JSONB
);
""")

cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS team_lineup JSONB;")
cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS timeline_extra JSONB;")
cur.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS gold_timeline JSONB;")

cur.execute("""
CREATE TABLE IF NOT EXISTS participants (
    id SERIAL PRIMARY KEY,
    match_id TEXT REFERENCES matches(match_id),
    puuid TEXT REFERENCES players(puuid),
    champion TEXT,
    role TEXT,
    win BOOLEAN,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    cs INTEGER,
    vision_score INTEGER,
    gold_earned INTEGER,
    damage_dealt INTEGER,
    damage_taken INTEGER,
    kill_participation REAL,
    damage_share REAL,
    turret_takedowns INTEGER,
    objectives_stolen INTEGER,
    solo_kills INTEGER,
    items JSONB,
    perks JSONB,
    death_positions JSONB,
    item_timeline JSONB,
    champ_level INTEGER,
    damage_rank INTEGER,
    gold_diff INTEGER,
    UNIQUE (match_id, puuid)
);
""")

# Für bereits bestehende Installationen: neue Spalten nachträglich ergänzen
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS damage_dealt INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS damage_taken INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS kill_participation REAL;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS damage_share REAL;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS turret_takedowns INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS objectives_stolen INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS solo_kills INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS items JSONB;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS perks JSONB;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS death_positions JSONB;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS item_timeline JSONB;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS champ_level INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS damage_rank INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS gold_diff INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS skill_order JSONB;")
# Für das Achievement-System der Gruppen-Ansicht (Penta/Quadra-Kills etc.) - liefert Riot
# direkt im Match-Objekt, kein Extra-Call nötig.
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS penta_kills INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS quadra_kills INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS triple_kills INTEGER;")
cur.execute("ALTER TABLE participants ADD COLUMN IF NOT EXISTS double_kills INTEGER;")

# Gruppen ("Community & Rivalen") - per Link teilbar, kein Login nötig: wer die Gruppen-ID
# kennt, kann sie sehen und Mitglieder verwalten (bewusst einfach gehalten, passend zum Rest
# der App ohne Nutzerkonten).
cur.execute("""
CREATE TABLE IF NOT EXISTS gruppen (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    erstellt_am TIMESTAMP DEFAULT now()
);
""")
cur.execute("""
CREATE TABLE IF NOT EXISTS gruppen_mitglieder (
    gruppe_id TEXT REFERENCES gruppen(id) ON DELETE CASCADE,
    puuid TEXT REFERENCES players(puuid),
    hinzugefuegt_am TIMESTAMP DEFAULT now(),
    PRIMARY KEY (gruppe_id, puuid)
);
""")

# Falls die Tabelle schon vor dem UNIQUE-Constraint existierte (CREATE TABLE IF NOT EXISTS
# greift dann nicht mehr): eventuelle Duplikate bereinigen und Constraint nachträglich ergänzen.
cur.execute("""
    DELETE FROM participants a USING participants b
    WHERE a.id > b.id AND a.match_id = b.match_id AND a.puuid = b.puuid;
""")
cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'participants_match_id_puuid_key'
        ) THEN
            ALTER TABLE participants ADD CONSTRAINT participants_match_id_puuid_key UNIQUE (match_id, puuid);
        END IF;
    END $$;
""")

conn.commit()
cur.close()
conn.close()

print("Tabellen wurden erfolgreich erstellt!")
