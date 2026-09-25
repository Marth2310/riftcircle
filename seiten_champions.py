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
                "key": champion,
                "name": info["name"] if info else champion,
                "icon": info["icon"] if info else champion_icon_url(champion),
                "spiele": spiele,
                "siege": siege,
                "winrate": round(siege / spiele * 100, 1),
                "pickrate": round(spiele / matches_gesamt * 100, 1) if matches_gesamt else 0.0,
            })
        liste.sort(key=lambda c: (c["pickrate"], c["winrate"]), reverse=True)
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
    cur.close()
    conn.close()

    champs = []
    for c in alle_champions:
        anzahl, siege = gespielt.get(c["key"], (0, 0))
        champs.append({
            **c,
            "anzahl_spiele": anzahl,
            "winrate": round(siege / anzahl * 100) if anzahl else None,
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


@app.route("/champion/<key>")
def champion_detail(key):
    champ = get_champion_by_key(key)
    if champ is None:
        abort(404)

    conn = get_connection()
    cur = conn.cursor()
    stats = berechne_champion_stats(cur, key)
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
        spells=spells,
    )
