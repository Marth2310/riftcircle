import json
import time

import requests

from riot_assets import REQUEST_TIMEOUT


class SummonerNotFound(Exception):
    """Riot-Account wurde nicht gefunden, oder seine Spiele konnten nicht geladen werden
    (z.B. weil er nicht auf EUW/Europe spielt - dieses Tool ist auf diese Region beschränkt)."""


def merke_spielernamen(cur, spieler):
    """Trägt [(puuid, name, tag), ...] in den Namens-Index für die Suche ohne Tag ein bzw.
    aktualisiert Namen (Riot-IDs können sich ändern) und den Zeitpunkt des letzten Auftauchens.
    Bots und Einträge ohne vollständige Riot-ID werden übersprungen."""
    werte = [
        (puuid, name, tag) for puuid, name, tag in spieler
        if puuid and puuid != "BOT" and name and tag
    ]
    if not werte:
        return
    cur.executemany("""
        INSERT INTO bekannte_spieler (puuid, riot_name, riot_tag, zuletzt_gesehen)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (puuid) DO UPDATE SET
            riot_name = EXCLUDED.riot_name, riot_tag = EXCLUDED.riot_tag, zuletzt_gesehen = now();
    """, werte)


def namen_aus_match(info):
    """(puuid, name, tag) aller Teilnehmer aus einem rohen match-v5-"info"-Objekt."""
    return [(p.get("puuid"), p.get("riotIdGameName"), p.get("riotIdTagline")) for p in info["participants"]]


def speichere_participant(cur, match_id, p, info):
    """Berechnet alle abgeleiteten Werte (Kill-Participation, Damage-Share, Damage-Rang,
    Gold-Diff zum Lane-Gegner, ...) für EINEN Teilnehmer `p` aus den rohen Match-Daten `info`
    und speichert die Zeile in participants (Upsert). Von sync_player() für den gesuchten
    Spieler genutzt, und von harvest_meta.py für alle 10 Teilnehmer eines Matches - dieselbe
    Berechnung, unabhängig davon wessen Match-Historie gerade synchronisiert wird."""
    team = [pp for pp in info["participants"] if pp["teamId"] == p["teamId"]]
    team_kills = sum(pp["kills"] for pp in team)
    team_damage = sum(pp["totalDamageDealtToChampions"] for pp in team)

    kill_participation = (p["kills"] + p["assists"]) / team_kills if team_kills > 0 else 0
    damage_share = p["totalDamageDealtToChampions"] / team_damage if team_damage > 0 else 0

    turret_takedowns = p.get("turretTakedowns", 0)
    objectives_stolen = p.get("objectivesStolen", 0)
    solo_kills = p.get("challenges", {}).get("soloKills", 0)

    items = [p.get(f"item{i}", 0) for i in range(7)]
    perks = p.get("perks", {})
    champ_level = p.get("champLevel", 0)

    alle_spieler = info["participants"]
    damage_sortiert = sorted(alle_spieler, key=lambda pp: pp["totalDamageDealtToChampions"], reverse=True)
    damage_rank = next(i for i, pp in enumerate(damage_sortiert, start=1) if pp["puuid"] == p["puuid"])
    lane_gegner = next(
        (pp for pp in alle_spieler
         if pp["teamId"] != p["teamId"] and p["teamPosition"] and pp["teamPosition"] == p["teamPosition"]),
        None
    )
    gold_diff = p["goldEarned"] - lane_gegner["goldEarned"] if lane_gegner else None

    cur.execute("""
        INSERT INTO participants
        (match_id, puuid, champion, role, win, kills, deaths, assists, cs, vision_score,
         gold_earned, damage_dealt, damage_taken, kill_participation, damage_share,
         turret_takedowns, objectives_stolen, solo_kills, items, perks, champ_level,
         damage_rank, gold_diff, penta_kills, quadra_kills, triple_kills, double_kills)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (match_id, puuid) DO UPDATE SET
            champion = EXCLUDED.champion,
            role = EXCLUDED.role,
            win = EXCLUDED.win,
            kills = EXCLUDED.kills,
            deaths = EXCLUDED.deaths,
            assists = EXCLUDED.assists,
            cs = EXCLUDED.cs,
            vision_score = EXCLUDED.vision_score,
            gold_earned = EXCLUDED.gold_earned,
            damage_dealt = EXCLUDED.damage_dealt,
            damage_taken = EXCLUDED.damage_taken,
            kill_participation = EXCLUDED.kill_participation,
            damage_share = EXCLUDED.damage_share,
            turret_takedowns = EXCLUDED.turret_takedowns,
            objectives_stolen = EXCLUDED.objectives_stolen,
            solo_kills = EXCLUDED.solo_kills,
            items = EXCLUDED.items,
            perks = EXCLUDED.perks,
            champ_level = EXCLUDED.champ_level,
            damage_rank = EXCLUDED.damage_rank,
            gold_diff = EXCLUDED.gold_diff,
            penta_kills = EXCLUDED.penta_kills,
            quadra_kills = EXCLUDED.quadra_kills,
            triple_kills = EXCLUDED.triple_kills,
            double_kills = EXCLUDED.double_kills;
    """, (
        match_id, p["puuid"], p["championName"], p["teamPosition"], p["win"],
        p["kills"], p["deaths"], p["assists"],
        p["totalMinionsKilled"] + p["neutralMinionsKilled"],
        p["visionScore"], p["goldEarned"],
        p["totalDamageDealtToChampions"], p["totalDamageTaken"],
        kill_participation, damage_share,
        turret_takedowns, objectives_stolen, solo_kills,
        json.dumps(items), json.dumps(perks), champ_level,
        damage_rank, gold_diff,
        p.get("pentaKills", 0), p.get("quadraKills", 0), p.get("tripleKills", 0), p.get("doubleKills", 0),
    ))


def sync_player(cur, conn, headers, riot_name, riot_tag, anzahl_matches=20):
    """Holt PUUID + die letzten `anzahl_matches` Spiele für einen Riot-Account und
    speichert alles in der DB. Bereits gespeicherte Matches werden übersprungen (kein
    erneuter Match-Detail-Call), damit wiederholte Suchen nach demselben Spieler schnell sind.

    Gibt (puuid, anzahl_neu_gespeicherter_matches) zurück.
    """
    url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{riot_name}/{riot_tag}"
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    account = resp.json()
    if resp.status_code != 200 or "puuid" not in account:
        raise SummonerNotFound(f"{riot_name}#{riot_tag} wurde nicht gefunden.")
    puuid = account["puuid"]
    # Offizielle Schreibweise von Riot statt der eingetippten (Groß-/Kleinschreibung)
    riot_name = account.get("gameName") or riot_name
    riot_tag = account.get("tagLine") or riot_tag

    # Match-IDs VOR dem Speichern des Spielers validieren: Accounts von anderen Servern
    # als EUW/Europe (z.B. KR, NA) liefern hier eine leere oder ungültige Liste - dann
    # lieber eine klare Fehlermeldung als einen kaputten/leeren Spieler-Eintrag in der DB.
    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={anzahl_matches}"
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    match_ids = resp.json()
    if resp.status_code != 200 or not isinstance(match_ids, list) or len(match_ids) == 0:
        raise SummonerNotFound(
            f"Keine Spiele für {riot_name}#{riot_tag} gefunden. "
            f"Dieses Tool unterstützt aktuell nur Accounts vom EUW-Server."
        )

    cur.execute("""
        INSERT INTO players (puuid, riot_name, riot_tag)
        VALUES (%s, %s, %s)
        ON CONFLICT (puuid) DO UPDATE SET
            riot_name = EXCLUDED.riot_name, riot_tag = EXCLUDED.riot_tag,
            -- Wer gezielt gesucht wird, ist ein echtes Profil - auch wenn er vorher schon als
            -- anonymer Teilnehmer über harvest_meta.py in der DB gelandet war
            is_meta_sample = FALSE;
    """, (puuid, riot_name, riot_tag))
    merke_spielernamen(cur, [(puuid, riot_name, riot_tag)])
    conn.commit()

    # Zeilen ohne "items"/"champ_level"/"damage_rank"/"penta_kills" stammen von vor der
    # jeweiligen Erweiterung und gelten als noch nicht vollständig synchronisiert, damit sie
    # automatisch nachgeladen werden. "gold_diff" bleibt bei ARAM legitim NULL, taugt daher
    # nicht als Marker.
    cur.execute(
        """SELECT match_id FROM participants
           WHERE puuid = %s AND items IS NOT NULL AND champ_level IS NOT NULL
             AND damage_rank IS NOT NULL AND penta_kills IS NOT NULL;""",
        (puuid,)
    )
    bereits_gespeichert = {row[0] for row in cur.fetchall()}
    neue_match_ids = [m for m in match_ids if m not in bereits_gespeichert]

    for match_id in neue_match_ids:
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}"
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            # Einzelner Match-Call fehlgeschlagen (z.B. abgelaufener Key mitten im Sync,
            # oder ein 404 für eine inzwischen ungültige Match-ID) - diesen Match überspringen
            # statt den ganzen Sync (und damit die Seite) abstürzen zu lassen. Bleibt in
            # "bereits_gespeichert" fehlend, wird beim nächsten Sync erneut versucht.
            time.sleep(1.2)
            continue
        info = resp.json()["info"]

        cur.execute("""
            INSERT INTO matches (match_id, played_at, duration_seconds, patch)
            VALUES (%s, to_timestamp(%s), %s, %s)
            ON CONFLICT (match_id) DO NOTHING;
        """, (match_id, info["gameStartTimestamp"] / 1000, info["gameDuration"], info["gameVersion"]))

        # Nur den Teil unseres Spielers aus dem Match rauspicken - die Namen aller 10 aber
        # für die Suche ohne Tag merken
        for p in info["participants"]:
            if p["puuid"] == puuid:
                speichere_participant(cur, match_id, p, info)
        merke_spielernamen(cur, namen_aus_match(info))

        conn.commit()
        time.sleep(1.2)  # Pause, um das Rate-Limit nicht zu sprengen

    return puuid, len(neue_match_ids)


ROLE_ORDER = {"TOP": 0, "JUNGLE": 1, "MIDDLE": 2, "BOTTOM": 3, "UTILITY": 4}


def fetch_match_teams(headers, match_id):
    """Holt alle 10 Spieler eines Matches (für die Team-Aufstellung in der Detailansicht) -
    live von der Match-API, wird nicht dauerhaft für alle Spieler in der DB gepflegt."""
    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}"
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return None

    info = resp.json()["info"]
    teams = {100: [], 200: []}
    for p in info["participants"]:
        teams[p["teamId"]].append({
            "puuid": p["puuid"],
            "participant_id": p["participantId"],
            "champion": p["championName"],
            "riot_name": p.get("riotIdGameName") or p.get("summonerName", "?"),
            "riot_tag": p.get("riotIdTagline", ""),
            "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "win": p["win"],
            "role": p.get("teamPosition", ""),
            "items": [p.get(f"item{i}", 0) for i in range(7)],
            "perks": p.get("perks", {}),
            "cs": p["totalMinionsKilled"] + p["neutralMinionsKilled"],
            "vision_score": p["visionScore"],
            "gold_earned": p["goldEarned"],
            "damage_dealt": p["totalDamageDealtToChampions"],
            "damage_taken": p["totalDamageTaken"],
            "wards_placed": p.get("wardsPlaced", 0),
        })

    for seite in teams.values():
        seite.sort(key=lambda p: ROLE_ORDER.get(p["role"], 99))

    return teams


def get_top_mastery_champion_ids(puuid, headers, anzahl=3):
    """championIds (numerisch) der `anzahl` Champions mit der höchsten Mastery - die "Mains"
    des Spielers, für den Profil-Hintergrund. Leere Liste, falls der Call fehlschlägt oder der
    Spieler noch keine Mastery-Punkte hat (z.B. brandneuer Account)."""
    url = (
        f"https://euw1.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
        f"/top?count={anzahl}"
    )
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return []
    daten = resp.json()
    return [eintrag["championId"] for eintrag in daten] if isinstance(daten, list) else []
