import requests

from riot_assets import REQUEST_TIMEOUT

# Ungefähre Ausdehnung der Summoner's-Rift-Weltkoordinaten (quadratische Karte).
MAP_SIZE = 14820


def fetch_timeline_events(headers, match_id, puuid):
    """Ein Timeline-Call liefert alles, was wir aus der Match-Timeline brauchen, zusammen
    (teurer Extra-Call, daher nur einmal pro Match und danach in der DB gecacht statt
    mehrfach separat abgefragt). Gibt ein Dict zurück:
      - deaths: eigene Tode mit Position + killer_id (für Death-Map & Zeitstrahl)
      - eigene_kills: eigene Kills (killerId = wir) mit Position + victim_id (für Zeitstrahl)
      - items: eigener Item-Kaufverlauf
      - gold_timeline: minütlicher Gold-Stand aller 10 Spieler (für den Lane-Vergleich)
      - objective_kills: alle Drachen/Herald/Baron-Kills im Match (mit Position, für
        Tod-vor-Objective-Analyse & Zeitstrahl)
      - alle_tode: JEDER Champion-Tod im Match, nur victim_id+timestamp (für Teamfight-Erkennung)
      - ward_platzierungen: jede WARD_PLACED im Match, nur creator_id+timestamp (Riot liefert
        dafür KEINE Position - Wards können daher nur zeitlich, nicht räumlich verglichen werden)
      - skill_order: eigene Skill-Level-Ups in Reihenfolge (1=Q, 2=W, 3=E, 4=R), nur "NORMAL"
        (kein EVOLVE), für die Champion-Datenbank (Skill-Prioritäten)
    None, falls der Call fehlschlägt oder der Spieler im Match nicht gefunden wird."""
    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        return None
    data = resp.json()

    participants = data.get("info", {}).get("participants", [])
    participant_id = next((p["participantId"] for p in participants if p["puuid"] == puuid), None)
    if participant_id is None:
        return None

    deaths = []
    eigene_kills = []
    items = []
    gold_timeline = []
    objective_kills = []
    alle_tode = []
    ward_platzierungen = []
    skill_order = []

    for frame in data.get("info", {}).get("frames", []):
        for event in frame.get("events", []):
            etype = event.get("type")
            timestamp = event.get("timestamp", 0)

            if etype == "CHAMPION_KILL":
                alle_tode.append({"victim_id": event.get("victimId"), "timestamp": timestamp})
                pos = event.get("position")
                if event.get("victimId") == participant_id and pos:
                    deaths.append({
                        "x": pos["x"], "y": pos["y"], "timestamp": timestamp,
                        "killer_id": event.get("killerId"),
                    })
                if event.get("killerId") == participant_id and pos:
                    eigene_kills.append({
                        "x": pos["x"], "y": pos["y"], "timestamp": timestamp,
                        "victim_id": event.get("victimId"),
                    })
            elif etype == "ITEM_PURCHASED" and event.get("participantId") == participant_id:
                items.append({"itemId": event.get("itemId"), "timestamp": timestamp})
            elif etype == "ELITE_MONSTER_KILL":
                pos = event.get("position")
                objective_kills.append({
                    "monster_type": event.get("monsterType"),
                    "monster_sub_type": event.get("monsterSubType"),
                    "killer_team_id": event.get("killerTeamId"),
                    "timestamp": timestamp,
                    "x": pos["x"] if pos else None,
                    "y": pos["y"] if pos else None,
                })
            elif etype == "WARD_PLACED":
                ward_platzierungen.append({"creator_id": event.get("creatorId"), "timestamp": timestamp})
            elif (
                etype == "SKILL_LEVEL_UP" and event.get("participantId") == participant_id
                and event.get("levelUpType") == "NORMAL"
            ):
                skill_order.append(event.get("skillSlot"))

        pf = frame.get("participantFrames", {})
        if pf:
            gold_timeline.append({
                "minute": frame.get("timestamp", 0) // 60000,
                "gold": {pid: info.get("totalGold", 0) for pid, info in pf.items()},
            })

    return {
        "deaths": deaths,
        "eigene_kills": eigene_kills,
        "items": items,
        "gold_timeline": gold_timeline,
        "objective_kills": objective_kills,
        "alle_tode": alle_tode,
        "ward_platzierungen": ward_platzierungen,
        "skill_order": skill_order,
    }


def death_position_percent(death):
    """Normalisiert eine Weltkoordinate auf 0-100% für die Anzeige, Y gespiegelt, damit die
    blaue Basis unten-links liegt (wie auf der In-Game-Minimap)."""
    x_pct = max(0, min(100, death["x"] / MAP_SIZE * 100))
    y_pct = max(0, min(100, 100 - death["y"] / MAP_SIZE * 100))
    return x_pct, y_pct


def format_game_time(timestamp_ms):
    seconds = timestamp_ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"
