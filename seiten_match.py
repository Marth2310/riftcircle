"""Match-Detailseite (Tode, Zeitstrahl, Team-/Lane-Vergleich) und 1-gegen-1-Vergleich."""
import json

from flask import abort, redirect, render_template, request, url_for

from app_core import app, headers
from champion_mobility import hat_escape
from db import get_connection
from riot_assets import champion_icon_url, get_ddragon_version, item_icon_url
from riot_fetch import fetch_match_teams, merke_spielernamen
from riot_runes import build_rune_display, keystone_and_secondary_icons
from riot_timeline import death_position_percent, fetch_timeline_events, format_game_time
from rollen_tipps import tipps_fuer_rolle
from vergleich import fazit_saetze, vergleiche


def berechne_rangliste(teams, duration_seconds):
    """Bewertet alle 10 Spieler mit einem zusammengesetzten Performance-Score (KDA,
    Kill-Participation, Damage-Share, Vision/Min, CS/Min, Gold - keine offizielle Riot-
    Metrik, sondern eine eigene, nachvollziehbare Annäherung) und vergibt Platz 1-10.
    MVP ist immer Platz 1. ACE ist der bestplatzierte Spieler des VERLIERENDEN Teams,
    aber nur wenn der unter den Top 5 der gesamten Lobby liegt ("hat sehr gut gespielt,
    aber trotzdem verloren") - sonst gibt es in diesem Spiel kein Ace.
    Gibt {puuid: {"rang": int, "ist_mvp": bool, "ist_ace": bool}} zurück."""
    minutes = max(duration_seconds / 60, 1)
    team_kills = {tid: sum(p["kills"] for p in spieler) for tid, spieler in teams.items()}
    team_damage = {tid: sum(p["damage_dealt"] for p in spieler) for tid, spieler in teams.items()}

    bewertet = []
    for team_id, spieler_liste in teams.items():
        for p in spieler_liste:
            kda = (p["kills"] + p["assists"]) / p["deaths"] if p["deaths"] > 0 else (p["kills"] + p["assists"])
            kp = (p["kills"] + p["assists"]) / team_kills[team_id] if team_kills[team_id] > 0 else 0
            dmg_share = p["damage_dealt"] / team_damage[team_id] if team_damage[team_id] > 0 else 0
            score = (
                kda * 2.5
                + kp * 8
                + dmg_share * 8
                + (p["vision_score"] / minutes) * 2
                + (p["cs"] / minutes) * 0.3
                + (p["gold_earned"] / 1000) * 0.4
            )
            bewertet.append({"puuid": p["puuid"], "score": score, "win": p["win"]})

    bewertet.sort(key=lambda p: p["score"], reverse=True)

    ergebnis = {}
    ace_vergeben = False
    for i, p in enumerate(bewertet):
        rang = i + 1
        ist_ace = not p["win"] and rang <= 5 and not ace_vergeben
        if ist_ace:
            ace_vergeben = True
        ergebnis[p["puuid"]] = {"rang": rang, "ist_mvp": rang == 1, "ist_ace": ist_ace}

    return ergebnis


OBJECTIVE_NAMEN = {
    "DRAGON": "Drachen", "RIFTHERALD": "Herald", "BARON_NASHOR": "Baron", "ELDER_DRAGON": "Elder-Drachen",
}
OBJECTIVE_FENSTER_MS = 90_000  # Tod bis zu 90s vor gegnerischem Objective-Kill wird als Ursache gewertet
TEAMFIGHT_FENSTER_MS = 15_000  # Tode innerhalb 15s um einen eigenen Tod gelten als "gemeinsamer" Fight
VISION_FENSTER_MS = 90_000  # eigene Ward "frisch", wenn sie <= 90s vor dem Tod gesetzt wurde


def analysiere_tode(deaths, timeline_extra, teams, puuid, champion):
    """Sucht in den eigenen Toden nach zwei Mustern und gibt dazu konkrete Kommentare zurück:
    1. Tod kurz bevor der Gegner ein Objective (Drache/Herald/Baron) erbeutet hat.
    2. Isolierter Tod (kein Teammate ist im selben Zeitfenster gestorben) ohne dass das Team
       in den letzten 90s eine neue Ward gesetzt hat, UND der Champion hat keine Fluchtfähigkeit.
    Riot liefert keine Positionen für Wards, daher ist "frische Vision" hier zeitlich (nicht
    räumlich) gemeint - siehe champion_mobility.py und riot_timeline.py für Details."""
    kommentare = []
    if not deaths or not teams or not timeline_extra:
        return kommentare

    mein_participant_id = None
    mein_team_id = None
    for team_id, spieler_liste in teams.items():
        for p in spieler_liste:
            if p["puuid"] == puuid:
                mein_participant_id = p["participant_id"]
                mein_team_id = team_id
    if mein_participant_id is None:
        return kommentare

    teammate_ids = {p["participant_id"] for p in teams[mein_team_id]}
    objective_kills = timeline_extra.get("objective_kills", [])
    alle_tode = timeline_extra.get("alle_tode", [])
    eigene_team_wards = sorted(
        w["timestamp"] for w in timeline_extra.get("ward_platzierungen", [])
        if w["creator_id"] in teammate_ids
    )

    for death in deaths:
        ts = death["timestamp"]

        for obj in objective_kills:
            if str(obj.get("killer_team_id")) == str(mein_team_id):
                continue
            delta = obj["timestamp"] - ts
            if 0 < delta <= OBJECTIVE_FENSTER_MS:
                name = OBJECTIVE_NAMEN.get(obj.get("monster_type"), "Objective")
                kommentare.append(
                    f"Du bist bei {format_game_time(ts)} gestorben - nur {delta // 1000}s später hat der "
                    f"Gegner den {name} erbeutet. Achte in solchen Zeitfenstern besonders auf sicheres "
                    f"Positionieren, statt das Objective-Gebiet ungesichert zu betreten."
                )
                break

        teamfight = any(
            t["victim_id"] != mein_participant_id and t["victim_id"] in teammate_ids
            and abs(t["timestamp"] - ts) <= TEAMFIGHT_FENSTER_MS
            for t in alle_tode
        )
        if teamfight:
            continue

        letzte_ward = max((w for w in eigene_team_wards if w <= ts), default=None)
        vision_frisch = letzte_ward is not None and (ts - letzte_ward) <= VISION_FENSTER_MS

        if not vision_frisch and not hat_escape(champion):
            kommentare.append(
                f"Du bist bei {format_game_time(ts)} isoliert gestorben (kein Teammate im selben Moment "
                f"beteiligt), und dein Team hatte in den letzten 90 Sekunden keine neue Ward gesetzt. "
                f"{champion} hat keine Fluchtfähigkeit - auf solchen Champions lohnt es sich besonders, vor "
                f"dem Vorrücken erst frische Vision zu legen oder in der Nähe von Teammates zu bleiben."
            )

    return kommentare[:5]


def champion_lookup(teams):
    """participant_id -> Champion-Name, aus der Team-Aufstellung."""
    if not teams:
        return {}
    return {p["participant_id"]: p["champion"] for seite in teams.values() for p in seite}


def baue_zeitstrahl(death_positions, timeline_extra, teams, puuid):
    """Baut einen chronologischen Zeitstrahl aus eigenen Toden, eigenen Kills, Objective-
    Kills (beider Teams) und eigenen Ward-Platzierungen. Einträge mit Positionsdaten sind
    auf der Death-Map klickbar hervorhebbar - Ward-Einträge nicht, da Riot dafür keine
    Position liefert (siehe riot_timeline.py)."""
    if not teams or not timeline_extra:
        return []

    mein_participant_id = None
    mein_team_id = None
    for team_id, spieler_liste in teams.items():
        for p in spieler_liste:
            if p["puuid"] == puuid:
                mein_participant_id = p["participant_id"]
                mein_team_id = team_id
    if mein_participant_id is None:
        return []

    champs = champion_lookup(teams)
    eintraege = []

    for death in death_positions:
        x_pct, y_pct = death_position_percent(death)
        killer = champs.get(death.get("killer_id"), "Unbekannt")
        eintraege.append({
            "typ": "tod", "label": "TOD", "farbe": "loss",
            "timestamp": death["timestamp"],
            "zeit": format_game_time(death["timestamp"]),
            "text": f"Gestorben (getötet von {killer})",
            "x": round(x_pct, 1), "y": round(y_pct, 1), "klickbar": True,
        })

    for kill in timeline_extra.get("eigene_kills", []):
        x_pct, y_pct = death_position_percent(kill)
        opfer = champs.get(kill.get("victim_id"), "Unbekannt")
        eintraege.append({
            "typ": "kill", "label": "KILL", "farbe": "win",
            "timestamp": kill["timestamp"],
            "zeit": format_game_time(kill["timestamp"]),
            "text": f"Kill auf {opfer}",
            "x": round(x_pct, 1), "y": round(y_pct, 1), "klickbar": True,
        })

    for obj in timeline_extra.get("objective_kills", []):
        if obj.get("x") is None:
            continue
        x_pct, y_pct = death_position_percent(obj)
        name = OBJECTIVE_NAMEN.get(obj.get("monster_type"), "Objective")
        team_text = "dein Team" if str(obj.get("killer_team_id")) == str(mein_team_id) else "Gegner"
        eintraege.append({
            "typ": "objective", "label": "OBJ", "farbe": "gold",
            "timestamp": obj["timestamp"],
            "zeit": format_game_time(obj["timestamp"]),
            "text": f"{name} erbeutet ({team_text})",
            "x": round(x_pct, 1), "y": round(y_pct, 1), "klickbar": True,
        })

    for ward in timeline_extra.get("ward_platzierungen", []):
        if ward.get("creator_id") != mein_participant_id:
            continue
        eintraege.append({
            "typ": "ward", "label": "WARD", "farbe": "accent2",
            "timestamp": ward["timestamp"],
            "zeit": format_game_time(ward["timestamp"]),
            "text": "Ward platziert",
            "x": None, "y": None, "klickbar": False,
        })

    eintraege.sort(key=lambda e: e["timestamp"])
    for i, eintrag in enumerate(eintraege):
        eintrag["id"] = f"ev{i}"
    return eintraege


TEAM_VERGLEICH_KATEGORIEN = [
    ("kills", "Champion-Kills", 0),
    ("gold_earned", "Gold", 0),
    ("damage_dealt", "Schaden", 0),
    ("wards_placed", "Wards", 0),
    ("damage_taken", "Erlittener Schaden", 0),
    ("cs", "CS", 0),
]


def _formatiere_zahl(wert, nachkomma=0):
    """Deutsche Tausender-Schreibweise mit Punkt, z.B. 23570 -> '23.570'."""
    return f"{wert:,.{nachkomma}f}".replace(",", ".")


def baue_team_vergleich(teams):
    """Stellt Sieger- vs. Verliererteam über 6 Kategorien gegenüber: pro Spieler ein Balken
    (skaliert auf den Höchstwert der Kategorie über alle 10 Spieler) plus ein Ring mit dem
    Team-Gesamtwert. None, falls die Team-Aufstellung nicht vorliegt oder nicht genau 2
    Teams mit je 5 Spielern hat (z.B. Remake)."""
    if not teams or len(teams) != 2:
        return None

    seiten = list(teams.values())
    sieger = next((s for s in seiten if s and s[0].get("win")), None)
    verlierer = next((s for s in seiten if s and not s[0].get("win")), None)
    if not sieger or not verlierer or len(sieger) != 5 or len(verlierer) != 5:
        return None

    kategorien = []
    for feld, label, nachkomma in TEAM_VERGLEICH_KATEGORIEN:
        sieger_werte = [{"champion_icon": p["champion_icon"], "wert": p.get(feld, 0)} for p in sieger]
        verlierer_werte = [{"champion_icon": p["champion_icon"], "wert": p.get(feld, 0)} for p in verlierer]
        max_wert = max([w["wert"] for w in sieger_werte + verlierer_werte] + [1])

        for w in sieger_werte + verlierer_werte:
            w["pct"] = round(w["wert"] / max_wert * 100) if max_wert else 0
            w["text"] = _formatiere_zahl(w["wert"], nachkomma)

        sieger_summe = sum(w["wert"] for w in sieger_werte)
        verlierer_summe = sum(w["wert"] for w in verlierer_werte)
        gesamt = sieger_summe + verlierer_summe

        kategorien.append({
            "label": label,
            "sieger": sieger_werte,
            "verlierer": verlierer_werte,
            "sieger_summe": _formatiere_zahl(sieger_summe, nachkomma),
            "verlierer_summe": _formatiere_zahl(verlierer_summe, nachkomma),
            "sieger_pct": round(sieger_summe / gesamt * 100) if gesamt else 50,
        })

    return kategorien


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


def _lade_vergleichsdaten(cur, match_id, puuid):
    cur.execute("""
        SELECT p.champion, p.role, p.win, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
               p.damage_dealt, p.gold_earned, m.duration_seconds, pl.riot_name, pl.riot_tag
        FROM participants p
        JOIN matches m ON p.match_id = m.match_id
        JOIN players pl ON p.puuid = pl.puuid
        WHERE p.match_id = %s AND p.puuid = %s;
    """, (match_id, puuid))
    row = cur.fetchone()
    if row is None:
        return None
    (champion, role, win, kills, deaths, assists, cs, vision_score,
     damage_dealt, gold_earned, duration_seconds, riot_name, riot_tag) = row
    kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)
    return {
        "match_id": match_id, "puuid": puuid, "name": f"{riot_name}#{riot_tag}",
        "riot_name": riot_name, "riot_tag": riot_tag,
        "champion": champion, "champion_icon": champion_icon_url(champion, get_ddragon_version()),
        "role": role, "win": win, "kills": kills, "deaths": deaths, "assists": assists,
        "kda": round(kda, 2), "cs": cs, "vision_score": vision_score,
        "damage_dealt": damage_dealt, "gold_earned": gold_earned,
        "dauer_min": duration_seconds // 60,
    }


@app.route("/vergleich")
def vergleich_ansehen():
    m1, p1 = request.args.get("m1", ""), request.args.get("p1", "")
    m2, p2 = request.args.get("m2", ""), request.args.get("p2", "")
    if not (m1 and p1 and m2 and p2):
        return redirect(url_for("landing"))

    conn = get_connection()
    cur = conn.cursor()
    a = _lade_vergleichsdaten(cur, m1, p1)
    b = _lade_vergleichsdaten(cur, m2, p2)
    cur.close()
    conn.close()

    if a is None or b is None:
        abort(404)

    zeilen = vergleiche(a, b)
    saetze = fazit_saetze(a, b, zeilen)

    return render_template("vergleich.html", a=a, b=b, zeilen=zeilen, saetze=saetze)


@app.route("/match/<match_id>")
def match_detail(match_id):
    puuid = request.args.get("puuid", "")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.champion, p.role, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
               m.duration_seconds, p.win, p.items, p.perks, p.death_positions, p.item_timeline,
               m.team_lineup, m.gold_timeline, m.timeline_extra, pl.riot_name, pl.riot_tag,
               p.skill_order
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
     items, perks, death_positions, item_timeline, team_lineup, gold_timeline, timeline_extra,
     riot_name, riot_tag, skill_order) = row

    # Timeline (Todes-Positionen + eigene Kills + Item-Kaufverlauf + Gold-Verlauf +
    # Objective-Kills + alle Tode + Ward-Platzierungen + Skill-Order) ist ein separater,
    # teurer API-Call - nur bei der ersten Detailansicht dieses Matches holen, danach in der
    # DB gecacht. "eigene_kills" fehlt in älteren Caches (vor der Zeitstrahl-Erweiterung),
    # skill_order fehlt in Caches vor der Champion-Datenbank - beides gilt dann als veraltet,
    # damit automatisch nachgeladen wird.
    if (
        death_positions is None or item_timeline is None or gold_timeline is None
        or timeline_extra is None or "eigene_kills" not in timeline_extra or skill_order is None
    ):
        tl = fetch_timeline_events(headers, match_id, puuid)
        if tl:
            death_positions, item_timeline, gold_timeline = tl["deaths"], tl["items"], tl["gold_timeline"]
            timeline_extra = {
                "objective_kills": tl["objective_kills"],
                "alle_tode": tl["alle_tode"],
                "ward_platzierungen": tl["ward_platzierungen"],
                "eigene_kills": tl["eigene_kills"],
            }
            skill_order = tl["skill_order"]
            cur.execute(
                "UPDATE participants SET death_positions = %s, item_timeline = %s, skill_order = %s "
                "WHERE match_id = %s AND puuid = %s;",
                (json.dumps(death_positions), json.dumps(item_timeline), json.dumps(skill_order), match_id, puuid)
            )
            cur.execute(
                "UPDATE matches SET gold_timeline = %s, timeline_extra = %s WHERE match_id = %s;",
                (json.dumps(gold_timeline), json.dumps(timeline_extra), match_id)
            )
            conn.commit()
        else:
            # API-Call fehlgeschlagen (z.B. abgelaufener Dev-Key, der alle 24h neu generiert
            # werden muss) - NICHT die eventuell schon gespeicherten guten Werte in der DB mit
            # leeren Platzhaltern überschreiben, nur für DIESE Anzeige sinnvolle Fallbacks setzen.
            death_positions = death_positions or []
            item_timeline = item_timeline or []
            gold_timeline = gold_timeline or []
            timeline_extra = timeline_extra or {}
            skill_order = skill_order or []

    # Team-Aufstellung (alle 10 Spieler) ist match-weit, nicht pro Spieler - einmal pro
    # Match cachen statt bei jedem Betrachter neu von der Riot-API zu holen. Ältere Caches
    # ohne "perks"/"participant_id"/"damage_taken" (vor der jeweiligen Erweiterung gespeichert)
    # gelten als veraltet.
    if team_lineup is None or any(
        "perks" not in p or "participant_id" not in p or "damage_taken" not in p
        for seite in team_lineup.values() for p in seite
    ):
        team_lineup = fetch_match_teams(headers, match_id)
        if team_lineup:
            # JSON-Objektschlüssel sind immer Strings - direkt angleichen, damit ein frisch
            # geholtes team_lineup genauso aussieht wie eines, das aus der DB zurückkommt.
            team_lineup = {str(k): v for k, v in team_lineup.items()}
            merke_spielernamen(cur, [
                (p["puuid"], p["riot_name"], p["riot_tag"]) for seite in team_lineup.values() for p in seite
            ])
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
        rangliste = berechne_rangliste(team_lineup, duration)
        teams = {}
        for team_id, spieler_liste in team_lineup.items():
            team_out = []
            for p in spieler_liste:
                keystone_icon, secondary_icon = keystone_and_secondary_icons(p.get("perks"), ddragon_version)
                bewertung = rangliste.get(p["puuid"], {})
                team_out.append({
                    **p,
                    "champion_icon": champion_icon_url(p["champion"], ddragon_version),
                    "item_icons": [item_icon_url(i, ddragon_version) for i in p.get("items", [0] * 7)],
                    "keystone_icon": keystone_icon,
                    "secondary_icon": secondary_icon,
                    "rang": bewertung.get("rang"),
                    "ist_mvp": bewertung.get("ist_mvp", False),
                    "ist_ace": bewertung.get("ist_ace", False),
                })
            teams[team_id] = team_out

    lane_vergleich = baue_lane_vergleich(teams, puuid, gold_timeline)
    rollen_tipps = tipps_fuer_rolle(role)
    tod_analyse = analysiere_tode(death_positions, timeline_extra, teams, puuid, champion)
    zeitstrahl = baue_zeitstrahl(death_positions, timeline_extra, teams, puuid)
    team_vergleich = baue_team_vergleich(teams)

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
        tod_analyse=tod_analyse,
        zeitstrahl=zeitstrahl,
        team_vergleich=team_vergleich,
        teams=teams,
        lane_vergleich=lane_vergleich,
        rollen_tipps=rollen_tipps,
    )
