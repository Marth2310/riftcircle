import requests

from riot_assets import REQUEST_TIMEOUT, get_ddragon_version

# Alle Champions aus Data Dragon - einmal pro Patch-Version gecacht, wie get_ddragon_version()
# in riot_assets.py. Anders als bei get_champion_tips (Riot liefert keine Meta-Statistiken),
# kommt die Champion-Liste selbst (Name, Rollen-Tags, Icon) direkt von Riot.
_champion_liste_cache = None  # (version, [champions])
_item_info_cache = None  # (version, {item_id: {"tags": {...}, "gold": int, "hat_upgrade": bool}})

SKILL_BUCHSTABEN = {1: "Q", 2: "W", 3: "E", 4: "R"}
MIN_SPIELE_FUER_BUILD = 3  # weniger als das gilt als Rauschen, kein eigener Build-Tab
MAX_BUILDS = 3


def _get_item_info():
    """item_id -> {tags, gold, hat_upgrade}. "hat_upgrade" = Data Dragon "into" ist nicht
    leer, d.h. das Item ist eine unfertige Zwischenstufe (z.B. Pickaxe, Dagger) und kein
    fertiges Enditem (z.B. Infinity Edge) - genau der Marker, den Riot selbst benutzt, um
    Komponenten von fertigen Items zu unterscheiden."""
    global _item_info_cache
    version = get_ddragon_version()
    if _item_info_cache is None or _item_info_cache[0] != version:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json"
        data = requests.get(url, timeout=REQUEST_TIMEOUT).json()["data"]
        info = {
            int(iid): {
                "tags": set(d.get("tags", [])),
                "gold": d.get("gold", {}).get("total", 0),
                "hat_upgrade": bool(d.get("into")),
            }
            for iid, d in data.items()
        }
        _item_info_cache = (version, info)
    return _item_info_cache[1]


def get_all_champions():
    global _champion_liste_cache
    version = get_ddragon_version()
    if _champion_liste_cache is None or _champion_liste_cache[0] != version:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
        data = requests.get(url, timeout=REQUEST_TIMEOUT).json()["data"]
        champions = sorted(
            (
                {
                    "key": c["id"],
                    "numeric_key": c["key"],
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


def get_champion_by_numeric_id(numeric_id):
    """championId (z.B. aus der Mastery-API, int) -> Champion-Key (z.B. "Kaisa")."""
    return next((c for c in get_all_champions() if c["numeric_key"] == str(numeric_id)), None)


def get_champion_by_key(key):
    return next((c for c in get_all_champions() if c["key"] == key), None)


def _fertige_items(items, item_info):
    """Nur abgeschlossene Items (keine Schuhe/Trinket/Tränke/unfertige Komponenten) - das
    sind die einzigen, die etwas über den BUILD eines Champions aussagen. Schuhe werden
    separat behandelt, alles andere fliegt komplett raus."""
    ergebnis = []
    for item_id in items or []:
        if not item_id:
            continue
        info = item_info.get(item_id)
        if not info or info["hat_upgrade"]:
            continue
        tags = info["tags"]
        if "Trinket" in tags or "Consumable" in tags or "Boots" in tags:
            continue
        ergebnis.append(item_id)
    return ergebnis


def _fertige_schuhe(items, item_info):
    return [i for i in (items or []) if i and item_info.get(i, {}).get("tags") and "Boots" in item_info[i]["tags"]]


BUILD_LABEL_REGELN = [
    ("Crit", {"CriticalStrike"}),
    ("Lethality", {"ArmorPenetration"}),
    ("AP", {"SpellDamage"}),
    ("Bruiser/Tank", {"Health", "Armor", "SpellBlock"}),
]


def _build_label(item_ids, item_info, vergeben):
    """Grober Build-Name aus den Item-Tags der Core-Items (Crit/Lethality/AP/Bruiser/AD) -
    dieselbe Unterscheidung, die U.GG mit eigenen Build-Kategorien macht, hier heuristisch
    aus Data-Dragon-Tags abgeleitet statt manuell gepflegt. Bei Mehrdeutigkeit oder
    Namenskollision (z.B. zwei Crit-Builds) wird durchnummeriert."""
    tags_vereint = set()
    for iid in item_ids:
        tags_vereint |= item_info.get(iid, {}).get("tags", set())

    basis = "AD"
    for name, benoetigte_tags in BUILD_LABEL_REGELN:
        if tags_vereint & benoetigte_tags:
            basis = name
            break

    if basis not in vergeben:
        vergeben.add(basis)
        return basis
    n = 2
    while f"{basis} {n}" in vergeben:
        n += 1
    label = f"{basis} {n}"
    vergeben.add(label)
    return label


def _item_liste(counter, gesamt, limit=6):
    top = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"item_id": iid, "count": cnt, "pct": round(cnt / gesamt * 100)} for iid, cnt in top]


def berechne_champion_stats(cur, champion_key):
    """Winrate, Item-Builds und Skill-Prioritäten für einen Champion - berechnet aus den
    Spielen, die bereits über RiftCircle synchronisiert wurden (nicht aus einer globalen
    Riot-Statistik, die gibt es über die persönliche API nicht - nur große Drittanbieter wie
    U.GG betreiben dafür eigene Scraping-Infrastruktur über Millionen Spiele). Wächst also mit
    der Zeit, je mehr Spiele mit diesem Champion getrackt werden. None bei 0 Spielen.

    Champions wie Kayn (Rhaast/Shadow Assassin) oder generell Champions mit mehreren
    populären Item-Strategien (Crit/Lethality/Bruiser/AP/Full AD) haben eben NICHT immer
    dieselben 2 Core-Items - deshalb werden Spiele anhand ihrer 2 teuersten fertigen Items
    in bis zu drei Build-Cluster gruppiert, statt alles in einen Topf zu werfen."""
    cur.execute(
        "SELECT win, items, skill_order, kills, deaths, assists FROM participants WHERE champion = %s;",
        (champion_key,)
    )
    rows = cur.fetchall()
    total = len(rows)
    if total == 0:
        return None

    item_info = _get_item_info()
    wins = sum(1 for win, *_ in rows if win)

    # Build-Signatur je Spiel: die 2 teuersten FERTIGEN Items (Gold-Gesamtwert), sortiert -
    # das sind praktisch immer genau die Items, die den Build definieren (Crit-Kern,
    # Lethality-Kern, ...). Spiele ganz ohne fertiges Item (sehr kurze Remakes) fließen nur
    # in die Gesamtstatistik ein, nicht in ein Build-Cluster.
    cluster = {}  # signature (tuple) -> {"rows": [...]}
    for row in rows:
        _, items, _, _, _, _ = row
        fertig = _fertige_items(items, item_info)
        if not fertig:
            continue
        fertig_sortiert = sorted(fertig, key=lambda i: item_info[i]["gold"], reverse=True)
        signatur = tuple(sorted(fertig_sortiert[:2]))
        cluster.setdefault(signatur, []).append(row)

    build_gruppen = sorted(cluster.items(), key=lambda kv: len(kv[1]), reverse=True)
    build_gruppen = [(sig, sig_rows) for sig, sig_rows in build_gruppen if len(sig_rows) >= MIN_SPIELE_FUER_BUILD]
    build_gruppen = build_gruppen[:MAX_BUILDS]

    vergebene_labels = set()
    builds = []
    for signatur, sig_rows in build_gruppen:
        sig_total = len(sig_rows)
        sig_wins = sum(1 for win, *_ in sig_rows if win)

        item_counter, boot_counter = {}, {}
        for _, items, _, _, _, _ in sig_rows:
            for iid in _fertige_items(items, item_info):
                item_counter[iid] = item_counter.get(iid, 0) + 1
            for iid in _fertige_schuhe(items, item_info):
                boot_counter[iid] = boot_counter.get(iid, 0) + 1

        builds.append({
            "label": _build_label(signatur, item_info, vergebene_labels),
            "games": sig_total,
            "winrate": round(sig_wins / sig_total * 100),
            "top_items": _item_liste(item_counter, sig_total),
            "top_boots": _item_liste(boot_counter, sig_total, limit=2),
        })

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
        "builds": builds,
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
