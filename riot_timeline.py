import requests

# Ungefähre Ausdehnung der Summoner's-Rift-Weltkoordinaten (quadratische Karte).
MAP_SIZE = 14820


def fetch_timeline_events(headers, match_id, puuid):
    """Ein Timeline-Call liefert Death-Positionen, Item-Kaufverlauf UND den minütlichen
    Gold-Stand aller 10 Spieler zusammen (teurer Extra-Call, daher nur einmal pro Match und
    danach in der DB gecacht statt mehrfach separat abgefragt).
    Gibt (deaths, item_purchases, gold_timeline) zurück - gold_timeline ist eine Liste von
    {"minute": int, "gold": {participant_id_als_string: gesamt_gold}} pro Frame."""
    url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{match_id}/timeline"
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return [], [], []
    data = resp.json()

    participants = data.get("info", {}).get("participants", [])
    participant_id = next((p["participantId"] for p in participants if p["puuid"] == puuid), None)
    if participant_id is None:
        return [], [], []

    deaths = []
    items = []
    gold_timeline = []
    for frame in data.get("info", {}).get("frames", []):
        for event in frame.get("events", []):
            etype = event.get("type")
            if etype == "CHAMPION_KILL" and event.get("victimId") == participant_id:
                pos = event.get("position")
                if pos:
                    deaths.append({"x": pos["x"], "y": pos["y"], "timestamp": event.get("timestamp", 0)})
            elif etype == "ITEM_PURCHASED" and event.get("participantId") == participant_id:
                items.append({"itemId": event.get("itemId"), "timestamp": event.get("timestamp", 0)})

        pf = frame.get("participantFrames", {})
        if pf:
            gold_timeline.append({
                "minute": frame.get("timestamp", 0) // 60000,
                "gold": {pid: info.get("totalGold", 0) for pid, info in pf.items()},
            })

    return deaths, items, gold_timeline


def death_position_percent(death):
    """Normalisiert eine Weltkoordinate auf 0-100% für die Anzeige, Y gespiegelt, damit die
    blaue Basis unten-links liegt (wie auf der In-Game-Minimap)."""
    x_pct = max(0, min(100, death["x"] / MAP_SIZE * 100))
    y_pct = max(0, min(100, 100 - death["y"] / MAP_SIZE * 100))
    return x_pct, y_pct


def format_game_time(timestamp_ms):
    seconds = timestamp_ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"
