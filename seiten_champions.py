"""Champion-Datenbank: Übersicht, Lane-Listen und Detailseite je Champion."""
from flask import abort, render_template, request

from app_core import app
from champion_stats import berechne_champion_stats, get_all_champions, get_champion_by_key
from db import get_connection
from riot_assets import (
    champion_icon_url,
    champion_splash_url,
    get_champion_spells,
    get_ddragon_version,
    item_icon_url,
    random_champion_splash_url,
    summoner_icon_url,
)
from riot_runes import build_rune_display


CHAMPION_SORTIERUNGEN = {
    "name": ("Name (A-Z)", lambda c: c["name"], False),
    "spiele_desc": ("Meiste Spiele", lambda c: c["anzahl_spiele"], True),
    "spiele_asc": ("Wenigste Spiele", lambda c: c["anzahl_spiele"], False),
    "winrate_desc": ("Höchste Winrate", lambda c: c["winrate"], True),
    "winrate_asc": ("Niedrigste Winrate", lambda c: c["winrate"], False),
}


# Kategorien der Champion-Datenbank: URL-Wert -> (Riot-teamPosition, Anzeigename, Icon-Name)
CHAMPION_ROLLEN = {
    "top": ("TOP", "Top", "top"),
    "jungle": ("JUNGLE", "Jungle", "jungle"),
    "mid": ("MIDDLE", "Mid", "middle"),
    "adc": ("BOTTOM", "ADC", "bottom"),
    "support": ("UTILITY", "Support", "utility"),
}
ROLLEN_ICON_URL = (
    "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-clash/global/default/assets/images/"
    "position-selector/positions/icon-position-{}.png"
)
# Unter dieser Spielzahl auf einer Position ausgeblendet - sonst stünde ein einzelnes
# gewonnenes Off-Role-Spiel (100% Winrate) beim Sortieren nach Winrate ganz oben.
MIN_SPIELE_JE_ROLLE = 5


# Tier-Liste je Rolle: die Winrate wird vorher Richtung 50% geglättet ("als hätte der Champion
# zusätzlich TIER_PRIOR_SPIELE Spiele mit genau 50% Winrate") - sonst stünde ein 3:0 mit 100%
# ganz oben, während ein Champion mit 54% aus 200 Spielen wirklich stark ist. Die Tiers selbst
# sind relativ (Anteil der Rolle), nicht feste Winrate-Grenzen: die Challenger-Stichproben der
# Meta-Statistik heben alle Winrates an, feste Grenzen machten sonst fast alles zu "S".
TIER_PRIOR_SPIELE = 50
TIER_ANTEILE = [("S", 0.10), ("A", 0.20), ("B", 0.40), ("C", 0.20)]  # Rest: D


def geglaettete_winrate(spiele, siege):
    return round((siege + TIER_PRIOR_SPIELE * 0.5) / (spiele + TIER_PRIOR_SPIELE) * 100, 2)


def tiers_vergeben(liste):
    """Setzt "tier" je Champion nach Rang der geglätteten Winrate innerhalb der Rolle."""
    # Gleichstand nach Pickrate - dieselbe Reihenfolge wie die Sortierung der Tabelle (JS)
    sortiert = sorted(liste, key=lambda c: (c["tier_wert"], c["pickrate"]), reverse=True)
    grenzen, summe = [], 0.0
    for name, anteil in TIER_ANTEILE:
        summe += anteil
        grenzen.append((round(summe * len(sortiert)), name))
    for i, c in enumerate(sortiert):
        c["tier"] = next((name for grenze, name in grenzen if i < grenze), "D")


# team_position (Riot-Wert) -> Icon-Name, fürs Rollen-Badge auf der Champion-Grid-Karte
ROLLEN_ICON_VON_POSITION = {pos: icon for _, (pos, _, icon) in CHAMPION_ROLLEN.items()}


def _haupt_rollen(cur):
    """Meistgespielte Lane je Champion (über alle Rollen hinweg) - fürs Rollen-Badge in der
    Grid-Ansicht der Champion-Datenbank."""
    cur.execute("""
        SELECT champion, role, COUNT(*)
        FROM participants
        WHERE role IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
        GROUP BY champion, role;
    """)
    bestwert = {}
    for champion, role, anzahl in cur.fetchall():
        bisher = bestwert.get(champion)
        if bisher is None or anzahl > bisher[1]:
            bestwert[champion] = (role, anzahl)
    return {champion: role for champion, (role, _) in bestwert.items()}


def _rollen_statistik(cur, team_position):
    """Winrate + Pickrate je Champion auf einer Position. Pickrate wie bei OP.GG/U.GG: in wie
    viel Prozent der Spiele (Matches) mit dieser Position der Champion dort gespielt wurde -
    nicht der Anteil an allen Zeilen, sonst würden Matches mit beiden gespeicherten
    Lane-Spielern (Challenger-Import) doppelt zählen."""
    cur.execute(
        "SELECT COUNT(DISTINCT match_id) FROM participants WHERE role = %s;", (team_position,)
    )
    matches_gesamt = cur.fetchone()[0]
    cur.execute("""
        SELECT champion, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END)
        FROM participants
        WHERE role = %s
        GROUP BY champion;
    """, (team_position,))
    zeilen = cur.fetchall()
    return matches_gesamt, zeilen


@app.route("/champions")
def champions():
    suche = request.args.get("q", "").strip().lower()
    sortierung = request.args.get("sort", "name")
    if sortierung not in CHAMPION_SORTIERUNGEN:
        sortierung = "name"
    rolle = request.args.get("rolle", "")
    alle_champions = get_all_champions()
    rollen_tabs = [
        {"key": key, "name": name, "icon": ROLLEN_ICON_URL.format(icon)}
        for key, (_, name, icon) in CHAMPION_ROLLEN.items()
    ]

    conn = get_connection()
    cur = conn.cursor()

    if rolle in CHAMPION_ROLLEN:
        matches_gesamt, zeilen = _rollen_statistik(cur, CHAMPION_ROLLEN[rolle][0])
        cur.close()
        conn.close()
        champ_info = {c["key"]: c for c in alle_champions}
        liste, ausgeblendet = [], 0
        for champion, spiele, siege in zeilen:
            if spiele < MIN_SPIELE_JE_ROLLE:
                ausgeblendet += 1
                continue
            info = champ_info.get(champion)
            liste.append({
                "tier_wert": geglaettete_winrate(spiele, siege),
                "key": champion,
                "name": info["name"] if info else champion,
                "icon": info["icon"] if info else champion_icon_url(champion),
                "spiele": spiele,
                "siege": siege,
                "winrate": round(siege / spiele * 100, 1),
                "pickrate": round(spiele / matches_gesamt * 100, 1) if matches_gesamt else 0.0,
            })
        tiers_vergeben(liste)
        liste.sort(key=lambda c: (c["tier_wert"], c["pickrate"]), reverse=True)
        return render_template(
            "champions.html", rolle=rolle, rollen_tabs=rollen_tabs,
            rollen_name=CHAMPION_ROLLEN[rolle][1], rollen_liste=liste,
            matches_gesamt=matches_gesamt, ausgeblendet=ausgeblendet,
            min_spiele=MIN_SPIELE_JE_ROLLE, suche=suche,
        )

    cur.execute(
        "SELECT champion, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END) FROM participants GROUP BY champion;"
    )
    gespielt = {champ: (anzahl, siege) for champ, anzahl, siege in cur.fetchall()}
    haupt_rollen = _haupt_rollen(cur)
    cur.close()
    conn.close()

    champs = []
    for c in alle_champions:
        anzahl, siege = gespielt.get(c["key"], (0, 0))
        rolle_position = haupt_rollen.get(c["key"])
        champs.append({
            **c,
            "anzahl_spiele": anzahl,
            "winrate": round(siege / anzahl * 100) if anzahl else None,
            "rolle_icon": ROLLEN_ICON_URL.format(ROLLEN_ICON_VON_POSITION[rolle_position]) if rolle_position else None,
        })
    if suche:
        champs = [c for c in champs if suche in c["name"].lower()]

    # Champions ohne getrackte Spiele haben keine sinnvolle Spiele-/Winrate-Kennzahl - bei
    # "meiste/wenigste Spiele" bzw. "Winrate" landen sie immer am Ende statt (bei "wenigste")
    # nutzlos ganz vorne, da sie alle bei 0 gleichauf lägen.
    _, sort_key, reverse = CHAMPION_SORTIERUNGEN[sortierung]
    if sortierung in ("spiele_asc", "winrate_desc", "winrate_asc", "spiele_desc"):
        mit_daten = [c for c in champs if c["anzahl_spiele"] > 0]
        ohne_daten = [c for c in champs if c["anzahl_spiele"] == 0]
        mit_daten.sort(key=sort_key, reverse=reverse)
        champs = mit_daten + sorted(ohne_daten, key=lambda c: c["name"])
    else:
        champs.sort(key=sort_key, reverse=reverse)

    return render_template(
        "champions.html", champions=champs, suche=suche, rolle="", rollen_tabs=rollen_tabs,
        sortierung=sortierung, sortierungen=CHAMPION_SORTIERUNGEN,
    )


MAX_TOP_SPIELER = 10


def _rollen_verteilung(cur, champion_key):
    """Auf welchen Positionen der Champion gespielt wird (Anteil + Winrate je Rolle)."""
    cur.execute("""
        SELECT role, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END)
        FROM participants
        WHERE champion = %s AND role IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
        GROUP BY role;
    """, (champion_key,))
    zeilen = {role: (n, siege) for role, n, siege in cur.fetchall()}
    gesamt = sum(n for n, _ in zeilen.values())
    verteilung = []
    for _, (position, name, icon) in CHAMPION_ROLLEN.items():
        n, siege = zeilen.get(position, (0, 0))
        if n:
            verteilung.append({
                "name": name, "icon": ROLLEN_ICON_URL.format(icon), "spiele": n,
                "anteil": round(n / gesamt * 100), "winrate": round(siege / n * 100),
            })
    verteilung.sort(key=lambda r: r["spiele"], reverse=True)
    return verteilung


def _top_spieler(cur, champion_key):
    """Die getrackten Spieler mit den meisten Spielen auf diesem Champion - ohne Meta-Stichproben
    (Challenger-Import), damit hier echte RiftCircle-Nutzer und ihre Freunde stehen."""
    cur.execute("""
        SELECT pl.riot_name, pl.riot_tag, pl.profile_icon_id, COUNT(*),
               SUM(CASE WHEN p.win THEN 1 ELSE 0 END), SUM(p.kills), SUM(p.deaths), SUM(p.assists)
        FROM participants p JOIN players pl ON pl.puuid = p.puuid
        WHERE p.champion = %s AND NOT COALESCE(pl.is_meta_sample, FALSE)
        GROUP BY pl.puuid, pl.riot_name, pl.riot_tag, pl.profile_icon_id
        ORDER BY COUNT(*) DESC, SUM(CASE WHEN p.win THEN 1 ELSE 0 END) DESC
        LIMIT %s;
    """, (champion_key, MAX_TOP_SPIELER))
    version = get_ddragon_version()
    return [
        {
            "name": name, "tag": tag,
            "icon": summoner_icon_url(icon_id, version) if icon_id else None,
            "spiele": n, "siege": siege, "winrate": round(siege / n * 100),
            "kda": round((k + a) / d, 2) if d else float(k + a),
        }
        for name, tag, icon_id, n, siege, k, d, a in cur.fetchall()
    ]


@app.route("/champion/<key>")
def champion_detail(key):
    champ = get_champion_by_key(key)
    if champ is None:
        abort(404)

    conn = get_connection()
    cur = conn.cursor()
    stats = berechne_champion_stats(cur, key)
    rollen = _rollen_verteilung(cur, key)
    top_spieler = _top_spieler(cur, key)
    cur.close()
    conn.close()

    ddragon_version = get_ddragon_version()
    if stats:
        for build in stats["builds"]:
            build["top_items"] = [
                {**it, "icon": item_icon_url(it["item_id"], ddragon_version)}
                for it in build["top_items"]
            ]
            build["top_boots"] = [
                {**it, "icon": item_icon_url(it["item_id"], ddragon_version)}
                for it in build["top_boots"]
            ]

        runen_varianten = []
        for v in stats["runen_varianten"]:
            runen = build_rune_display(v["perks"], ddragon_version, v["anteile"], v["stat_anteile"])
            if not runen:
                continue  # z.B. Keystone aus einem inzwischen entfernten Runenbaum
            keystone = next((r for r in runen["primary_rows"][0] if r["selected"]), None)
            if keystone is None:
                continue
            runen_varianten.append({**v, "runen": runen, "keystone": keystone})
        stats["runen_varianten"] = runen_varianten

    # Zufälliger Skin als Seitenhintergrund - bei Data-Dragon-Problemen einfach der Standard-Skin
    try:
        hintergrund_splash = random_champion_splash_url(key)
    except Exception:
        hintergrund_splash = champion_splash_url(key)
    try:
        spells = get_champion_spells(key)
    except Exception:
        spells = None  # Skill-Pfad erscheint dann nur mit Q/W/E/R-Buchstaben

    return render_template(
        "champion_detail.html", champ=champ, stats=stats, hintergrund_splash=hintergrund_splash,
        spells=spells, rollen=rollen, top_spieler=top_spieler,
        banner=champion_splash_url(key),
    )
