import requests

from riot_assets import get_ddragon_version

# Alle Champions aus Data Dragon - einmal pro Patch-Version gecacht, wie get_ddragon_version()
# in riot_assets.py. Anders als bei get_champion_tips (Riot liefert keine Meta-Statistiken),
# kommt die Champion-Liste selbst (Name, Rollen-Tags, Icon) direkt von Riot.
_champion_liste_cache = None  # (version, [champions])
_item_tags_cache = None  # (version, {item_id: {tags}})

SKILL_BUCHSTABEN = {1: "Q", 2: "W", 3: "E", 4: "R"}


def _get_item_tags():
    """item_id -> Set von Data-Dragon-Tags (u.a. "Boots"/"Trinket"/"Consumable") - damit
    Schuhe/Wards/Tränke aus der Item-Häufigkeit rausgefiltert werden können, die sonst jede
    Build-Statistik dominieren würden (die kauft ja jeder, unabhängig vom Champion)."""
    global _item_tags_cache
    version = get_ddragon_version()
    if _item_tags_cache is None or _item_tags_cache[0] != version:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json"
        data = requests.get(url).json()["data"]
        tags = {int(iid): set(info.get("tags", [])) for iid, info in data.items()}
        _item_tags_cache = (version, tags)
    return _item_tags_cache[1]


def get_all_champions():
    global _champion_liste_cache
    version = get_ddragon_version()
    if _champion_liste_cache is None or _champion_liste_cache[0] != version:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
        data = requests.get(url).json()["data"]
        champions = sorted(
            (
                {
                    "key": c["id"],
                    "name": c["name"],
                    "title": c["title"],
                    "tags": c["tags"],
                    "icon": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{c['id']}.png",
                }
                for c in data.values()
            ),
            key=lambda c: c["name"],
        )
        _champion_liste_cache = (version, champions)
    return _champion_liste_cache[1]


def get_champion_by_key(key):
    return next((c for c in get_all_champions() if c["key"] == key), None)


def berechne_champion_stats(cur, champion_key):
    """Winrate, meistgebaute Items und Skill-Prioritäten für einen Champion - berechnet aus
    den Spielen, die bereits über RiftCircle synchronisiert wurden (nicht aus einer globalen
    Riot-Statistik, die gibt es über die persönliche API nicht - nur große Drittanbieter wie
    U.GG betreiben dafür eigene Scraping-Infrastruktur über Millionen Spiele). Wächst also mit
    der Zeit, je mehr Spiele mit diesem Champion getrackt werden. None bei 0 Spielen."""
    cur.execute(
        "SELECT win, items, skill_order, kills, deaths, assists FROM participants WHERE champion = %s;",
        (champion_key,)
    )
    rows = cur.fetchall()
    total = len(rows)
    if total == 0:
        return None

    wins = sum(1 for win, *_ in rows if win)

    # Items zählen, aber Schuhe/Trinket/Tränke rausfiltern - die kauft jeder Champion
    # gleichermaßen und würden sonst jede Build-Statistik dominieren, ohne etwas über den
    # Champion selbst auszusagen. Schuhe zeigen wir separat (meistgekaufte Schuhe).
    item_tags = _get_item_tags()
    item_counter = {}
    boot_counter = {}
    for _, items, _, _, _, _ in rows:
        if not items:
            continue
        for item_id in items:
            if not item_id:
                continue
            tags = item_tags.get(item_id, set())
            if "Trinket" in tags or "Consumable" in tags:
                continue
            if "Boots" in tags:
                boot_counter[item_id] = boot_counter.get(item_id, 0) + 1
            else:
                item_counter[item_id] = item_counter.get(item_id, 0) + 1
    top_items = sorted(item_counter.items(), key=lambda kv: kv[1], reverse=True)[:6]
    top_boots = sorted(boot_counter.items(), key=lambda kv: kv[1], reverse=True)[:2]

    order_counter = {}
    skill_order_spiele = 0
    for _, _, skill_order, _, _, _ in rows:
        if not skill_order:
            continue
        skill_order_spiele += 1
        # Priorität = Reihenfolge, in der Q/W/E (R folgt eigenen Regeln) zuerst 5 Punkte
        # erreichen - fehlende Skills (z.B. bei sehr kurzen Spielen) hängen ans Ende.
        punkte = {1: 0, 2: 0, 3: 0}
        erreicht = []
        for slot in skill_order:
            if slot not in punkte:
                continue
            punkte[slot] += 1
            if punkte[slot] == 5 and slot not in erreicht:
                erreicht.append(slot)
        for slot in (1, 2, 3):
            if slot not in erreicht:
                erreicht.append(slot)
        prioritaet = " > ".join(SKILL_BUCHSTABEN[s] for s in erreicht)
        order_counter[prioritaet] = order_counter.get(prioritaet, 0) + 1
    top_orders = sorted(order_counter.items(), key=lambda kv: kv[1], reverse=True)[:3]

    gesamt_kills = sum(k for _, _, _, k, _, _ in rows)
    gesamt_deaths = sum(d for _, _, _, _, d, _ in rows)
    gesamt_assists = sum(a for _, _, _, _, _, a in rows)
    avg_kda = (
        (gesamt_kills + gesamt_assists) / gesamt_deaths if gesamt_deaths > 0
        else float(gesamt_kills + gesamt_assists)
    )

    return {
        "total_games": total,
        "wins": wins,
        "winrate": round(wins / total * 100),
        "top_items": [{"item_id": iid, "count": cnt, "pct": round(cnt / total * 100)} for iid, cnt in top_items],
        "top_boots": [{"item_id": iid, "count": cnt, "pct": round(cnt / total * 100)} for iid, cnt in top_boots],
        "skill_order_spiele": skill_order_spiele,
        "top_orders": [
            {"order": o, "count": cnt, "pct": round(cnt / skill_order_spiele * 100)}
            for o, cnt in top_orders
        ] if skill_order_spiele else [],
        "avg_kills": round(gesamt_kills / total, 1),
        "avg_deaths": round(gesamt_deaths / total, 1),
        "avg_assists": round(gesamt_assists / total, 1),
        "avg_kda": round(avg_kda, 2),
    }
