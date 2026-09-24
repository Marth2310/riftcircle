import requests

from riot_assets import REQUEST_TIMEOUT

# Data Dragon liefert die Rune-Bäume über runesReforged.json, aber NICHT die Stat-Shards
# (die kleinen +Stats-Bonusse) - deren IDs/Icons/Zeilen sind seit Jahren stabil und hier
# fest hinterlegt (bestätigt über https://darkintaqt.com/blog/perk-ids).
STAT_PERKS = {
    5008: {"name": "Adaptive Force", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsAdaptiveForceIcon.png"},
    5005: {"name": "Attack Speed", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsAttackSpeedIcon.png"},
    5007: {"name": "Ability Haste", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsCDRScalingIcon.png"},
    5010: {"name": "Move Speed", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsMovementSpeedIcon.png"},
    5001: {"name": "Health Scaling", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsHealthScalingIcon.png"},
    5011: {"name": "Health", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsHealthPlusIcon.png"},
    5013: {"name": "Tenacity/Slow Resist", "icon": "https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/StatModsTenacityIcon.png"},
}
# Reihen wie im Client: Offense / Flex / Defense, je 3 Auswahlmöglichkeiten
STAT_SHARD_ROWS = [
    [5008, 5005, 5007],
    [5008, 5010, 5001],
    [5011, 5013, 5001],
]

_tree_cache = {}


def _icon_url(path):
    return f"https://ddragon.leagueoflegends.com/cdn/img/{path}"


def get_rune_trees(version):
    """style_id -> voller Baum (name, icon, slots[].runes[]) aus runesReforged.json."""
    if version in _tree_cache:
        return _tree_cache[version]
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/runesReforged.json"
    trees = requests.get(url, timeout=REQUEST_TIMEOUT).json()
    by_id = {tree["id"]: tree for tree in trees}
    _tree_cache[version] = by_id
    return by_id


def build_rune_display(perks, version, anteile=None, stat_anteile=None):
    """Baut die komplette Rune-Page (alle Optionen je Zeile, gewählte markiert) - wie die
    Rune-Ansicht im LoL-Client, nicht nur eine Liste der gewählten Runen.
    Optional (Champion-Datenbank): anteile = {rune_id: pct} für Primär-/Sekundärbaum und
    stat_anteile = [{shard_id: pct}] je Stat-Zeile - landen als "pct" an jeder Rune (sonst None).
    Stat-Shards brauchen eigene Dicts pro Zeile, weil z.B. Adaptive Force in zwei Zeilen wählbar ist."""
    anteile = anteile or {}
    stat_anteile = stat_anteile or [{}, {}, {}]
    if not perks or not perks.get("styles") or len(perks["styles"]) < 2:
        return None

    trees = get_rune_trees(version)
    primary_data = perks["styles"][0]
    secondary_data = perks["styles"][1]
    primary_tree = trees.get(primary_data["style"])
    secondary_tree = trees.get(secondary_data["style"])
    if not primary_tree or not secondary_tree:
        return None

    selected_primary = {s["perk"] for s in primary_data["selections"]}
    selected_secondary = {s["perk"] for s in secondary_data["selections"]}

    def build_rows(tree, selected_ids, skip_first_row=False):
        rows = []
        for i, slot in enumerate(tree["slots"]):
            if skip_first_row and i == 0:
                continue  # Keystone-Zeile ist im Sekundärbaum nicht wählbar
            rows.append([
                {
                    "id": r["id"],
                    "name": r["name"],
                    "icon": _icon_url(r["icon"]),
                    "selected": r["id"] in selected_ids,
                    "pct": anteile.get(r["id"]),
                }
                for r in slot["runes"]
            ])
        return rows

    # Wichtig: pro Zeile gegen die für GENAU DIESE Zeile gewählte Rune vergleichen, nicht
    # gegen eine gemeinsame Menge aller drei Picks - sonst zählt z.B. Adaptive Force (5008,
    # wählbar in Offense UND Flex) fälschlich in beiden Zeilen als "gewählt", sobald es in
    # irgendeiner der beiden tatsächlich gepickt wurde (führte zu 5 statt 3 markierten Runen).
    stat_slot_picks = [
        perks["statPerks"]["offense"],
        perks["statPerks"]["flex"],
        perks["statPerks"]["defense"],
    ]
    stat_rows = [
        [
            {**STAT_PERKS.get(pid, {"name": f"Stat #{pid}", "icon": None}), "selected": pid == pick,
             "pct": zeilen_anteile.get(pid)}
            for pid in row
        ]
        for row, pick, zeilen_anteile in zip(STAT_SHARD_ROWS, stat_slot_picks, stat_anteile)
    ]

    return {
        "primary_name": primary_tree["name"],
        "primary_icon": _icon_url(primary_tree["icon"]),
        "primary_rows": build_rows(primary_tree, selected_primary),
        "secondary_name": secondary_tree["name"],
        "secondary_icon": _icon_url(secondary_tree["icon"]),
        "secondary_rows": build_rows(secondary_tree, selected_secondary, skip_first_row=True),
        "stat_rows": stat_rows,
    }


def keystone_and_secondary_icons(perks, version):
    """Kompakte Variante von build_rune_display: nur Keystone- + Sekundärbaum-Icon, für
    platzsparende Anzeigen (Match-Übersicht, Team-Aufstellung)."""
    runen = build_rune_display(perks, version)
    if not runen:
        return None, None
    keystone_icon = next((r["icon"] for r in runen["primary_rows"][0] if r["selected"]), None)
    return keystone_icon, runen["secondary_icon"]
