import json
import os
from datetime import datetime

import psycopg2
from dotenv import load_dotenv
from flask import Flask, abort, render_template, request

from analysis import (
    ANZAHL_MATCHES,
    KATEGORIEN,
    MATCH_QUERY,
    format_wert,
    get_player_tier,
    match_metrics,
    ringe_fuer_match,
    top_probleme,
)
from benchmarks import normalize_tier
from riot_assets import (
    champion_icon_url,
    champion_splash_url,
    get_ddragon_version,
    get_summoner_icon_id,
    item_icon_url,
    summoner_icon_url,
)
from riot_fetch import SummonerNotFound, fetch_match_teams, sync_player
from riot_runes import build_rune_display, keystone_and_secondary_icons
from riot_timeline import death_position_percent, fetch_timeline_events, format_game_time

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

app = Flask(__name__)


def get_connection():
    return psycopg2.connect(
        host="localhost", port=5432, dbname="lolanalytics",
        user="postgres", password=os.environ["DB_PASSWORD"]
    )


def baue_lane_vergleich(teams, puuid, gold_timeline):
    """Findet den Lane-Gegner (gleiche Rolle, anderes Team) und vergleicht mehrere Stats
    inkl. Gold-Differenz-Verlauf über die Zeit. None, falls kein Lane-Gegner existiert
    (z.B. ARAM ohne Rollen, oder Team-Aufstellung konnte nicht geladen werden)."""
    if not teams:
        return None

    alle = [p for seite in teams.values() for p in seite]
    mein_eintrag = next((p for p in alle if p["puuid"] == puuid), None)
    if not mein_eintrag or not mein_eintrag.get("role"):
        return None

    lane_gegner = next(
        (p for p in alle if p["puuid"] != puuid and p["role"] == mein_eintrag["role"]), None
    )
    if not lane_gegner:
        return None

    def kda_wert(p):
        return (p["kills"] + p["assists"]) / p["deaths"] if p["deaths"] > 0 else (p["kills"] + p["assists"])

    stat_defs = [
        ("KDA", kda_wert(mein_eintrag), kda_wert(lane_gegner), lambda v: f"{v:.2f}"),
        ("CS", mein_eintrag["cs"], lane_gegner["cs"], lambda v: f"{v:.0f}"),
        ("Gold", mein_eintrag["gold_earned"], lane_gegner["gold_earned"], lambda v: f"{v / 1000:.1f}k"),
        ("Damage", mein_eintrag["damage_dealt"], lane_gegner["damage_dealt"], lambda v: f"{v / 1000:.1f}k"),
        ("Vision", mein_eintrag["vision_score"], lane_gegner["vision_score"], lambda v: f"{v:.0f}"),
    ]
    stats = []
    for label, mein_wert, gegner_wert, fmt in stat_defs:
        gesamt = mein_wert + gegner_wert
        stats.append({
            "label": label,
            "mein_text": fmt(mein_wert),
            "gegner_text": fmt(gegner_wert),
            "mein_pct": round(mein_wert / gesamt * 100) if gesamt else 50,
            "mein_besser": mein_wert >= gegner_wert,
        })

    gold_verlauf = []
    if gold_timeline:
        mein_pid = str(mein_eintrag["participant_id"])
        gegner_pid = str(lane_gegner["participant_id"])
        for frame in gold_timeline:
            gold = frame.get("gold", {})
            if mein_pid in gold and gegner_pid in gold:
                gold_verlauf.append({"minute": frame["minute"], "diff": gold[mein_pid] - gold[gegner_pid]})

    return {"gegner": lane_gegner, "stats": stats, "gold_verlauf": gold_verlauf}


def relative_zeit(played_at):
    if played_at is None:
        return ""
    stunden = (datetime.now() - played_at).total_seconds() / 3600
    if stunden < 1:
        return "vor wenigen Minuten"
    if stunden < 24:
        n = int(stunden)
        return f"vor {n} Stunde{'n' if n != 1 else ''}"
    tage = int(stunden / 24)
    return f"vor {tage} Tag{'en' if tage != 1 else ''}"


@app.route("/")
def index():
    riot_id_input = request.args.get("riot_id", "").strip()
    fehler = None
    puuid = None

    conn = get_connection()
    cur = conn.cursor()

    if riot_id_input:
        if "#" in riot_id_input:
            gesuchter_name, gesuchter_tag = riot_id_input.rsplit("#", 1)
            gesuchter_name, gesuchter_tag = gesuchter_name.strip(), gesuchter_tag.strip()
            geraten = False
        else:
            # Riot bietet keine "alle Accounts mit diesem Namen"-Suche an - nur exakte
            # Name#Tag-Lookups. Viele frühe EUW-Accounts hatten beim Umstieg auf Riot IDs
            # automatisch den Tag "EUW" bekommen, das probieren wir als Best-Effort-Fallback.
            gesuchter_name, gesuchter_tag = riot_id_input.strip(), "EUW"
            geraten = True

        try:
            # Holt bei Bedarf neue Spiele nach - bereits gespeicherte Matches werden
            # übersprungen, wiederholte Suchen nach demselben Profil sind daher schnell.
            puuid, _ = sync_player(cur, conn, headers, gesuchter_name, gesuchter_tag)
        except SummonerNotFound as e:
            if geraten:
                fehler = (
                    f'Riot erlaubt leider keine Suche über alle Tags hinweg - wir haben automatisch '
                    f'"{gesuchter_name}#EUW" probiert: {e}'
                    f' Bitte gib den genauen Tag an, z.B. {gesuchter_name}#1234.'
                )
            else:
                fehler = str(e)
    else:
        cur.execute("SELECT puuid FROM players ORDER BY riot_name LIMIT 1;")
        row = cur.fetchone()
        if row:
            puuid = row[0]

    # Bereits getrackte Profile für den Schnellzugriff im Dashboard
    cur.execute("SELECT riot_name, riot_tag FROM players ORDER BY riot_name;")
    bekannte_spieler = [{"name": n, "tag": t} for n, t in cur.fetchall()]

    if puuid is None:
        cur.close()
        conn.close()
        return render_template(
            "dashboard.html",
            kein_spieler=True,
            fehler=fehler,
            riot_id_input=riot_id_input,
            bekannte_spieler=bekannte_spieler,
        )

    cur.execute("SELECT riot_name, riot_tag FROM players WHERE puuid = %s;", (puuid,))
    riot_name, riot_tag = cur.fetchone()

    tier, rank = get_player_tier(puuid, headers)
    anzeige_rang = f"{tier} {rank}" if tier else "Unranked"
    tier = normalize_tier(tier) if tier else "GOLD"

    cur.execute(MATCH_QUERY, (puuid, ANZAHL_MATCHES))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    ddragon_version = get_ddragon_version()
    profile_icon_id = get_summoner_icon_id(puuid, headers)

    spiele = []
    alle_vergleiche = []
    siege = 0

    for row in rows:
        werte, vergleich = match_metrics(row, tier)
        alle_vergleiche.append(vergleich)
        werte["champion_icon"] = champion_icon_url(werte["champion"], ddragon_version)
        werte["champion_splash"] = champion_splash_url(werte["champion"])
        werte["ringe"] = ringe_fuer_match(vergleich)
        werte["item_icons"] = [item_icon_url(i, ddragon_version) for i in werte["items"]]
        werte["zeit_text"] = relative_zeit(werte["played_at"])
        werte["keystone_icon"], werte["secondary_icon"] = keystone_and_secondary_icons(
            werte["perks"], ddragon_version
        )
        if werte["win"]:
            siege += 1
        spiele.append(werte)

    probleme = [
        {
            "label": KATEGORIEN[key]["label"],
            "tipp": KATEGORIEN[key]["tipp"],
            "unter_anzahl": stats["unter_anzahl"],
            "total": stats["total"],
            "avg_wert": format_wert(key, stats["avg_wert"]),
            "avg_richtwert": format_wert(key, stats["avg_richtwert"]),
            "erreicht_prozent": round(stats["avg_wert"] / stats["avg_richtwert"] * 100) if stats["avg_richtwert"] else 0,
        }
        for key, stats in top_probleme(alle_vergleiche)
    ]

    # Chart-Daten: chronologisch (älteste zuerst), da die DB-Abfrage neueste zuerst liefert
    chronologisch = list(reversed(spiele))
    kda_chart = {
        "labels": [s["champion"] for s in chronologisch],
        "kda": [round(s["kda"], 2) for s in chronologisch],
        "farben": ["#34d399" if s["win"] else "#f76c8a" for s in chronologisch],
    }

    return render_template(
        "dashboard.html",
        kein_spieler=False,
        fehler=fehler,
        riot_id_input=riot_id_input,
        bekannte_spieler=bekannte_spieler,
        puuid=puuid,
        riot_name=riot_name,
        riot_tag=riot_tag,
        rang=anzeige_rang,
        summoner_icon=summoner_icon_url(profile_icon_id, ddragon_version) if profile_icon_id else None,
        anzahl_spiele=len(spiele),
        siege=siege,
        niederlagen=len(spiele) - siege,
        spiele=spiele,
        probleme=probleme,
        kda_chart=kda_chart,
    )


@app.route("/match/<match_id>")
def match_detail(match_id):
    puuid = request.args.get("puuid", "")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.champion, p.role, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
               m.duration_seconds, p.win, p.items, p.perks, p.death_positions, p.item_timeline,
               m.team_lineup, m.gold_timeline, pl.riot_name, pl.riot_tag
        FROM participants p
        JOIN matches m ON p.match_id = m.match_id
        JOIN players pl ON p.puuid = pl.puuid
        WHERE p.match_id = %s AND p.puuid = %s;
    """, (match_id, puuid))
    row = cur.fetchone()

    if row is None:
        cur.close()
        conn.close()
        abort(404)

    (champion, role, kills, deaths, assists, cs, vision, duration, win,
     items, perks, death_positions, item_timeline, team_lineup, gold_timeline,
     riot_name, riot_tag) = row

    # Timeline (Todes-Positionen + Item-Kaufverlauf + minütlicher Gold-Stand aller 10
    # Spieler) ist ein separater, teurer API-Call - nur bei der ersten Detailansicht dieses
    # Matches holen, danach in der DB gecacht.
    if death_positions is None or item_timeline is None or gold_timeline is None:
        death_positions, item_timeline, gold_timeline = fetch_timeline_events(headers, match_id, puuid)
        cur.execute(
            "UPDATE participants SET death_positions = %s, item_timeline = %s WHERE match_id = %s AND puuid = %s;",
            (json.dumps(death_positions), json.dumps(item_timeline), match_id, puuid)
        )
        cur.execute(
            "UPDATE matches SET gold_timeline = %s WHERE match_id = %s;",
            (json.dumps(gold_timeline), match_id)
        )
        conn.commit()

    # Team-Aufstellung (alle 10 Spieler) ist match-weit, nicht pro Spieler - einmal pro
    # Match cachen statt bei jedem Betrachter neu von der Riot-API zu holen. Ältere Caches
    # ohne "perks"/"participant_id" (vor der jeweiligen Erweiterung gespeichert) gelten als veraltet.
    if team_lineup is None or any(
        "perks" not in p or "participant_id" not in p for seite in team_lineup.values() for p in seite
    ):
        team_lineup = fetch_match_teams(headers, match_id)
        if team_lineup:
            # JSON-Objektschlüssel sind immer Strings - direkt angleichen, damit ein frisch
            # geholtes team_lineup genauso aussieht wie eines, das aus der DB zurückkommt.
            team_lineup = {str(k): v for k, v in team_lineup.items()}
        cur.execute(
            "UPDATE matches SET team_lineup = %s WHERE match_id = %s;",
            (json.dumps(team_lineup), match_id)
        )
        conn.commit()

    cur.close()
    conn.close()

    ddragon_version = get_ddragon_version()
    minutes = duration / 60
    kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)

    # Items/Runen fehlen bei Matches, die vor dem entsprechenden Update gespeichert wurden -
    # ein erneuter Sync (Suche/Reload) holt sie automatisch nach (siehe riot_fetch.py).
    item_slots = [item_icon_url(i, ddragon_version) for i in (items or [0] * 7)]
    runen = build_rune_display(perks, ddragon_version)

    todesmarker = []
    for death in death_positions:
        x_pct, y_pct = death_position_percent(death)
        todesmarker.append({
            "x": round(x_pct, 1), "y": round(y_pct, 1),
            "zeit": format_game_time(death["timestamp"]),
        })

    item_build = [
        {
            "icon": item_icon_url(kauf["itemId"], ddragon_version),
            "zeit": format_game_time(kauf["timestamp"]),
        }
        for kauf in sorted(item_timeline, key=lambda k: k["timestamp"])
        if kauf.get("itemId")
    ]

    teams = None
    if team_lineup:
        teams = {}
        for team_id, spieler_liste in team_lineup.items():
            team_out = []
            for p in spieler_liste:
                keystone_icon, secondary_icon = keystone_and_secondary_icons(p.get("perks"), ddragon_version)
                team_out.append({
                    **p,
                    "champion_icon": champion_icon_url(p["champion"], ddragon_version),
                    "item_icons": [item_icon_url(i, ddragon_version) for i in p.get("items", [0] * 7)],
                    "keystone_icon": keystone_icon,
                    "secondary_icon": secondary_icon,
                })
            teams[team_id] = team_out

    lane_vergleich = baue_lane_vergleich(teams, puuid, gold_timeline)

    return render_template(
        "match_detail.html",
        match_id=match_id,
        puuid=puuid,
        riot_name=riot_name,
        riot_tag=riot_tag,
        champion=champion,
        champion_icon=champion_icon_url(champion, ddragon_version),
        role=role,
        win=win,
        kills=kills, deaths=deaths, assists=assists,
        kda=kda,
        cs=cs, cs_per_min=cs / minutes,
        vision=vision, vision_per_min=vision / minutes,
        item_slots=item_slots,
        item_build=item_build,
        runen=runen,
        todesmarker=todesmarker,
        teams=teams,
        lane_vergleich=lane_vergleich,
    )


if __name__ == "__main__":
    # Port 5000 kollidiert auf macOS oft mit dem AirPlay-Receiver-Dienst
    app.run(debug=True, port=5050)
