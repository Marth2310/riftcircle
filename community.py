"""Community-Bereiche der Startseite, die auch Besucher ohne Konto sehen: Highlights über alle
getrackten Spieler, die öffentlichen Top-Gruppen und ein Meta-Snapshot je Rolle. Alles drei
sind Aggregate über die ganze Datenbank - pro Gunicorn-Worker für CACHE_SEKUNDEN zwischen-
gespeichert, damit die meistbesuchte Seite nicht bei jedem Aufruf neu rechnet."""
import time

from flask import url_for

from achievements import bestes_achievement
from riot_assets import champion_icon_url, champion_splash_url
from seiten_champions import CHAMPION_ROLLEN, ROLLEN_ICON_URL
from champion_stats import get_champion_by_key
from seiten_gruppen import oeffentliche_rangliste
from web_hilfen import relative_zeit

CACHE_SEKUNDEN = 600
_cache = {}


def _gecacht(schluessel, berechnen):
    eintrag = _cache.get(schluessel)
    if eintrag and time.time() - eintrag[0] < CACHE_SEKUNDEN:
        return eintrag[1]
    wert = berechnen()
    _cache[schluessel] = (time.time(), wert)
    return wert


# Erst die letzte Woche; ist dort zu wenig los (getrackt wird nur, wer gesucht wird), wird das
# Fenster schrittweise größer - die Überschrift nennt den tatsächlichen Zeitraum.
HIGHLIGHT_FENSTER_TAGE = (7, 30, 90)
MIN_HIGHLIGHTS = 3
MAX_HIGHLIGHTS = 6
KLASSEN_RANG = {"legendaer": 0, "episch": 1, "achievement": 2}


def _highlights_im_fenster(cur, tage):
    cur.execute("""
        SELECT p.match_id, p.puuid, pl.riot_name, pl.riot_tag, p.champion, p.win,
               p.kills, p.deaths, p.assists, p.cs, p.damage_dealt, p.damage_rank,
               p.objectives_stolen, p.solo_kills, p.penta_kills, p.quadra_kills, p.triple_kills,
               p.double_kills, p.inhibitoren_verloren, p.vision_score, p.damage_share,
               p.gold_diff, p.turret_takedowns, p.objective_monopol, m.played_at, m.duration_seconds
        FROM participants p
        JOIN matches m ON m.match_id = p.match_id
        JOIN players pl ON pl.puuid = p.puuid
        WHERE NOT COALESCE(pl.is_meta_sample, FALSE)
          AND m.played_at >= now() - %s * interval '1 day'
        ORDER BY m.played_at DESC
        LIMIT 400;
    """, (tage,))
    kandidaten = []
    for row in cur.fetchall():
        (match_id, puuid, name, tag, champion, win, kills, deaths, assists, cs, damage_dealt,
         damage_rank, objectives_stolen, solo_kills, penta, quadra, triple, double,
         inhibitoren_verloren, vision_score, damage_share, gold_diff, turret_takedowns,
         objective_monopol, played_at, duration_seconds) = row
        achievement = bestes_achievement({
            "champion": champion, "win": win, "kills": kills, "deaths": deaths, "assists": assists,
            "damage_dealt": damage_dealt or 0, "damage_rank": damage_rank,
            "objectives_stolen": objectives_stolen or 0, "solo_kills": solo_kills or 0,
            "penta_kills": penta or 0, "quadra_kills": quadra or 0, "triple_kills": triple or 0,
            "double_kills": double or 0, "inhibitoren_verloren": inhibitoren_verloren,
            "vision_score": vision_score, "damage_share": damage_share, "gold_diff": gold_diff,
            "turret_takedowns": turret_takedowns, "objective_monopol": objective_monopol,
            "cs": cs, "duration_seconds": duration_seconds,
        })
        if not achievement:
            continue
        kandidaten.append({
            "achievement": achievement,
            "name": name, "tag": tag, "champion": champion,
            "kills": kills, "deaths": deaths, "assists": assists,
            "champion_icon": champion_icon_url(champion),
            "champion_splash": champion_splash_url(champion),
            "zeit_text": relative_zeit(played_at),
            "url": url_for("match_detail", match_id=match_id, puuid=puuid),
        })
    # Stärkste Klasse zuerst, je Art und je Spieler höchstens einmal - sonst füllt ein einziger
    # Vielspieler mit fünf "Solo-Kills"-Karten den ganzen Bereich
    kandidaten.sort(key=lambda h: KLASSEN_RANG.get(h["achievement"]["tier"], 9))
    gewaehlt, arten, spieler = [], set(), set()
    for h in kandidaten:
        if h["achievement"]["id"] in arten or h["name"] in spieler:
            continue
        gewaehlt.append(h)
        arten.add(h["achievement"]["id"])
        spieler.add(h["name"])
    for h in kandidaten:
        if len(gewaehlt) >= MAX_HIGHLIGHTS:
            break
        if h not in gewaehlt and h["achievement"]["id"] not in arten:
            gewaehlt.append(h)
            arten.add(h["achievement"]["id"])
    return gewaehlt[:MAX_HIGHLIGHTS]


def community_highlights(cur):
    """{"tage": n, "liste": [...]} - die stärksten Achievements des kleinsten Zeitfensters,
    in dem genug passiert ist."""
    def berechnen():
        highlights = []
        for tage in HIGHLIGHT_FENSTER_TAGE:
            highlights = _highlights_im_fenster(cur, tage)
            if len(highlights) >= MIN_HIGHLIGHTS:
                return {"tage": tage, "liste": highlights}
        return {"tage": HIGHLIGHT_FENSTER_TAGE[-1], "liste": highlights}
    return _gecacht("highlights", berechnen)


TOP_GRUPPEN = 3


def top_gruppen(cur):
    return _gecacht("top_gruppen", lambda: oeffentliche_rangliste(cur, limit=TOP_GRUPPEN))


# Wie viele Spiele ein Champion auf einer Rolle mindestens braucht, um im Meta-Snapshot
# aufzutauchen - darunter ist die Winrate zu zufällig (ein 3:0 wäre sonst "100% Winrate")
MIN_SPIELE_META = 25


def meta_snapshot(cur):
    """Je Rolle der Champion mit der höchsten Winrate (ab MIN_SPIELE_META Spielen) aus allen
    gespeicherten Spielen - dieselbe Datenbasis wie die Champion-Datenbank."""
    def berechnen():
        cur.execute("""
            SELECT role, champion, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END)
            FROM participants
            WHERE role IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
            GROUP BY role, champion
            HAVING COUNT(*) >= %s;
        """, (MIN_SPIELE_META,))
        beste = {}
        for role, champion, spiele, siege in cur.fetchall():
            winrate = siege / spiele * 100
            if role not in beste or winrate > beste[role]["winrate_roh"]:
                beste[role] = {"champion": champion, "spiele": spiele, "winrate_roh": winrate}
        snapshot = []
        for rolle_key, (position, rollen_name, icon) in CHAMPION_ROLLEN.items():
            eintrag = beste.get(position)
            if not eintrag:
                continue
            info = get_champion_by_key(eintrag["champion"])
            snapshot.append({
                "rolle_key": rolle_key,
                "rolle": rollen_name,
                "rollen_icon": ROLLEN_ICON_URL.format(icon),
                "champion": eintrag["champion"],
                "name": info["name"] if info else eintrag["champion"],
                "icon": champion_icon_url(eintrag["champion"]),
                "splash": champion_splash_url(eintrag["champion"]),
                "spiele": eintrag["spiele"],
                "winrate": round(eintrag["winrate_roh"], 1),
            })
        return snapshot
    return _gecacht("meta", berechnen)
