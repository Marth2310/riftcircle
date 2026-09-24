import json
import os
import secrets
import urllib.parse
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, abort, make_response, redirect, render_template, request, url_for

from achievements import bestes_achievement
from analysis import (
    ANZAHL_MATCHES,
    KATEGORIEN,
    MATCH_QUERY,
    berechne_note,
    format_wert,
    get_player_ranks,
    match_metrics,
    ringe_fuer_match,
    top_probleme,
    top_staerken,
    vergleichssatz,
)
from benchmarks import normalize_tier
from champion_stats import (
    berechne_champion_stats,
    get_all_champions,
    get_champion_by_key,
    get_champion_by_numeric_id,
)
from db import get_connection
from champion_mobility import hat_escape
from riot_assets import (
    champion_icon_url,
    champion_splash_url,
    get_ddragon_version,
    get_summoner_icon_id,
    item_icon_url,
    random_champion_splash_url,
    summoner_icon_url,
)
from riot_fetch import SummonerNotFound, fetch_match_teams, get_top_mastery_champion_id, sync_player
from riot_runes import build_rune_display, keystone_and_secondary_icons
from riot_timeline import death_position_percent, fetch_timeline_events, format_game_time
from rollen_tipps import tipps_fuer_rolle
from vergleich import fazit_saetze, vergleiche
from wochenrueckblick import berechne_score

load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}

app = Flask(__name__)




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


AVATAR_FARBEN = ["var(--void)", "var(--accent2)", "var(--win)", "var(--gold)", "var(--void-2)", "var(--accent)"]

ZULETZT_GESEHEN_COOKIE = "zuletzt_gesehen"
MAX_ZULETZT_GESEHEN = 10


def _lese_zuletzt_gesehen():
    """Zuletzt von DIESEM Browser gesuchte Profile - rein über ein Cookie, keine Server-
    Session/Login nötig. Jeder Besucher sieht nur seine eigene Liste (vorher war "bekannte
    Spieler" global für alle sichtbar - das war explizit nicht gewünscht).
    Der Cookie-Wert ist selbst URL-kodiert (statt Werkzeug/dem Client das Quoting des rohen
    JSON überlassen - manche HTTP-Clients verschlucken sich an verschachtelten
    Anführungszeichen in einem gequoteten Cookie-Wert)."""
    roh = request.cookies.get(ZULETZT_GESEHEN_COOKIE)
    if not roh:
        return []
    try:
        daten = json.loads(urllib.parse.unquote(roh))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(daten, list):
        return []
    return [e for e in daten if isinstance(e, dict) and {"puuid", "name", "tag"} <= e.keys()][:MAX_ZULETZT_GESEHEN]


def _mit_farbe(eintraege):
    return [
        {**e, "farbe": AVATAR_FARBEN[sum(ord(c) for c in e["name"]) % len(AVATAR_FARBEN)]}
        for e in eintraege
    ]


def _neue_zuletzt_gesehen_liste(puuid, riot_name, riot_tag):
    """Aktuell angesehenes Profil ganz nach vorne (dedupliziert per puuid), auf
    MAX_ZULETZT_GESEHEN begrenzt. Reine Berechnung (kein Response nötig), damit sich die neue
    Liste sowohl fürs Rendern als auch fürs Cookie-Schreiben wiederverwenden lässt."""
    bisherige = [e for e in _lese_zuletzt_gesehen() if e["puuid"] != puuid]
    return ([{"puuid": puuid, "name": riot_name, "tag": riot_tag}] + bisherige)[:MAX_ZULETZT_GESEHEN]


def _cookie_setzen(resp, liste):
    """Ein Jahr gültig, httponly (Server-only, kein JS-Zugriff nötig)."""
    resp.set_cookie(
        ZULETZT_GESEHEN_COOKIE, urllib.parse.quote(json.dumps(liste)),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
    )
    return resp


MEINE_GRUPPEN_COOKIE = "meine_gruppen"
MAX_MEINE_GRUPPEN = 20


def _lese_meine_gruppen():
    """Gruppen, die DIESER Browser erstellt oder besucht hat - genau wie zuletzt_gesehen rein
    übers Cookie. Die Gruppe selbst lebt in der DB und ist über ihre ID für jeden mit dem Link
    sichtbar (Community/Rivalen-Gedanke, kein Login) - das Cookie merkt sich nur, welche
    Gruppen-Links DIESER Browser kennt, für den Schnellzugriff auf Startseite/Profil."""
    roh = request.cookies.get(MEINE_GRUPPEN_COOKIE)
    if not roh:
        return []
    try:
        daten = json.loads(urllib.parse.unquote(roh))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(daten, list):
        return []
    # "icon" fehlt in Cookies von vor dieser Erweiterung - Default-Schild als Fallback statt
    # eines kaputten leeren Icons.
    return [
        {"icon": "🛡️", **e} for e in daten if isinstance(e, dict) and {"id", "name"} <= e.keys()
    ][:MAX_MEINE_GRUPPEN]


def _meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon="🛡️"):
    bisherige = [e for e in _lese_meine_gruppen() if e["id"] != gruppe_id]
    neu = ([{"id": gruppe_id, "name": name, "icon": icon}] + bisherige)[:MAX_MEINE_GRUPPEN]
    resp.set_cookie(
        MEINE_GRUPPEN_COOKIE, urllib.parse.quote(json.dumps(neu)),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
    )
    return resp


@app.route("/riot.txt")
def riot_verification():
    """Domain-Verifizierung für die Riot-Production-API-Key-Bewerbung - der Verifizierungscode
    muss unter https://riftcircle.com/riot.txt erreichbar sein, nichts weiter im Response."""
    return "08e82a1c-c99b-47d6-b243-f819bd38890e", 200, {"Content-Type": "text/plain"}


@app.route("/")
def landing():
    zuletzt_gesehen = _mit_farbe(_lese_zuletzt_gesehen())
    meine_gruppen = _lese_meine_gruppen()
    return render_template(
        "landing.html", zuletzt_gesehen=zuletzt_gesehen, meine_gruppen=meine_gruppen,
        gruppen_icons=GRUPPEN_ICONS,
    )


CHAMPION_SORTIERUNGEN = {
    "name": ("Name (A-Z)", lambda c: c["name"], False),
    "spiele_desc": ("Meiste Spiele", lambda c: c["anzahl_spiele"], True),
    "spiele_asc": ("Wenigste Spiele", lambda c: c["anzahl_spiele"], False),
    "winrate_desc": ("Höchste Winrate", lambda c: c["winrate"], True),
    "winrate_asc": ("Niedrigste Winrate", lambda c: c["winrate"], False),
}


@app.route("/champions")
def champions():
    suche = request.args.get("q", "").strip().lower()
    sortierung = request.args.get("sort", "name")
    if sortierung not in CHAMPION_SORTIERUNGEN:
        sortierung = "name"
    alle_champions = get_all_champions()

    conn = get_connection()
    cur = conn.cursor()
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
        "champions.html", champions=champs, suche=suche,
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

    return render_template("champion_detail.html", champ=champ, stats=stats)


GRUPPEN_ICONS = ["🛡️", "⚔️", "🔥", "🐉", "👑", "🎯", "💀", "🏆", "⚡", "🌙", "🦂", "🩸"]


@app.route("/gruppen/neu", methods=["POST"])
def gruppe_erstellen():
    """Erstellt eine neue, per Link teilbare Gruppe ("Community & Rivalen") - kein Login
    nötig, wer den Link zur Gruppe kennt, kann sie sehen und Mitglieder verwalten."""
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("landing"))
    icon = request.form.get("icon", "").strip()
    if icon not in GRUPPEN_ICONS:
        icon = GRUPPEN_ICONS[0]

    gruppe_id = secrets.token_urlsafe(6)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO gruppen (id, name, icon) VALUES (%s, %s, %s);", (gruppe_id, name, icon))
    conn.commit()
    cur.close()
    conn.close()

    resp = make_response(redirect(url_for("gruppe_ansehen", gruppe_id=gruppe_id)))
    return _meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon)


GRUPPEN_FEED_LIMIT = 40


def baue_gruppen_feed(cur, mitglied_puuids):
    """Letzte Spiele ALLER Gruppenmitglieder, chronologisch gemischt (nicht pro Mitglied
    getrennt) - genau das "ich seh die Spiele der anderen direkt hier"-Gefühl, das eine
    Gruppe von einer reinen Mitgliederliste unterscheidet. Erkennt nebenbei Achievements
    (Pentakill, perfektes Spiel, ...) pro Zeile für den Highlight-Bereich."""
    if not mitglied_puuids:
        return []

    ddragon_version = get_ddragon_version()
    cur.execute("""
        SELECT p.match_id, p.puuid, pl.riot_name, pl.riot_tag, p.champion, p.role, p.win,
               p.kills, p.deaths, p.assists, p.cs, p.damage_dealt, p.damage_rank,
               p.objectives_stolen, p.solo_kills,
               p.penta_kills, p.quadra_kills, p.triple_kills, p.double_kills,
               m.played_at, m.duration_seconds
        FROM participants p
        JOIN matches m ON p.match_id = m.match_id
        JOIN players pl ON p.puuid = pl.puuid
        WHERE p.puuid = ANY(%s)
        ORDER BY m.played_at DESC
        LIMIT %s;
    """, (mitglied_puuids, GRUPPEN_FEED_LIMIT))

    feed = []
    for row in cur.fetchall():
        (match_id, puuid, riot_name, riot_tag, champion, role, win, kills, deaths, assists,
         cs, damage_dealt, damage_rank, objectives_stolen, solo_kills, penta, quadra, triple, double,
         played_at, duration_seconds) = row

        achievement_zeile = {
            "champion": champion, "win": win, "kills": kills, "deaths": deaths, "assists": assists,
            "damage_dealt": damage_dealt or 0, "damage_rank": damage_rank,
            "objectives_stolen": objectives_stolen or 0,
            "solo_kills": solo_kills or 0, "penta_kills": penta or 0, "quadra_kills": quadra or 0,
            "triple_kills": triple or 0, "double_kills": double or 0,
        }

        kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)

        feed.append({
            "match_id": match_id, "puuid": puuid, "riot_name": riot_name, "riot_tag": riot_tag,
            "champion": champion, "champion_icon": champion_icon_url(champion, ddragon_version),
            "champion_splash": champion_splash_url(champion),
            "role": role, "win": win, "kills": kills, "deaths": deaths, "assists": assists,
            "kda": round(kda, 2), "cs": cs, "damage_dealt": damage_dealt,
            "zeit_text": relative_zeit(played_at), "dauer_min": duration_seconds // 60,
            "achievement": bestes_achievement(achievement_zeile),
        })
    return feed


def baue_wochenrueckblick(cur, mitglieder):
    """Wer war diese Woche (letzte 7 Tage) am besten? Siehe wochenrueckblick.py für die
    Gewichtung. Leere Liste, falls in der Gruppe diese Woche noch niemand gespielt hat."""
    if not mitglieder:
        return []

    puuids = [m["puuid"] for m in mitglieder]
    cur.execute("""
        SELECT p.puuid,
               COUNT(*) AS spiele,
               SUM(CASE WHEN p.win THEN 1 ELSE 0 END) AS siege,
               SUM(p.kills) AS kills, SUM(p.deaths) AS deaths, SUM(p.assists) AS assists,
               SUM(COALESCE(p.penta_kills, 0)) AS pentas, SUM(COALESCE(p.quadra_kills, 0)) AS quadras,
               SUM(COALESCE(p.triple_kills, 0)) AS triples, SUM(COALESCE(p.double_kills, 0)) AS doubles
        FROM participants p
        JOIN matches m ON p.match_id = m.match_id
        WHERE p.puuid = ANY(%s) AND m.played_at >= now() - interval '7 days'
        GROUP BY p.puuid;
    """, (puuids,))

    mitglied_by_puuid = {m["puuid"]: m for m in mitglieder}
    rangliste = []
    for row in cur.fetchall():
        (puuid, spiele, siege, kills, deaths, assists, pentas, quadras, triples, doubles) = row
        stats = {
            "spiele": spiele, "siege": siege, "kills": kills, "deaths": deaths, "assists": assists,
            "pentas": pentas, "quadras": quadras, "triples": triples, "doubles": doubles,
        }
        avg_kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)
        mitglied = mitglied_by_puuid.get(puuid, {})
        rangliste.append({
            "puuid": puuid, "name": mitglied.get("name", "?"), "tag": mitglied.get("tag", ""),
            "farbe": mitglied.get("farbe", "var(--void)"),
            "spiele": spiele, "siege": siege, "winrate": round(siege / spiele * 100),
            "avg_kda": round(avg_kda, 2),
            "pentas": pentas, "quadras": quadras, "triples": triples, "doubles": doubles,
            "score": berechne_score(stats),
        })
    rangliste.sort(key=lambda r: r["score"], reverse=True)
    return rangliste


@app.route("/gruppe/<gruppe_id>")
def gruppe_ansehen(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, icon FROM gruppen WHERE id = %s;", (gruppe_id,))
    row = cur.fetchone()
    if row is None:
        cur.close()
        conn.close()
        abort(404)
    name, icon = row
    icon = icon or "🛡️"

    cur.execute("""
        SELECT pl.puuid, pl.riot_name, pl.riot_tag
        FROM gruppen_mitglieder gm JOIN players pl ON gm.puuid = pl.puuid
        WHERE gm.gruppe_id = %s
        ORDER BY gm.hinzugefuegt_am;
    """, (gruppe_id,))
    mitglieder_rows = cur.fetchall()
    mitglieder = _mit_farbe([{"puuid": p, "name": n, "tag": t} for p, n, t in mitglieder_rows])

    feed = baue_gruppen_feed(cur, [p for p, _, _ in mitglieder_rows])
    achievement_feed = [f for f in feed if f["achievement"]]
    wochenrangliste = baue_wochenrueckblick(cur, mitglieder)

    cur.close()
    conn.close()

    resp = make_response(render_template(
        "gruppe.html", gruppe_id=gruppe_id, gruppe_name=name, gruppe_icon=icon, mitglieder=mitglieder,
        feed=feed, achievement_feed=achievement_feed, wochenrangliste=wochenrangliste,
        gruppen_icons=GRUPPEN_ICONS,
    ))
    # Wer den Link öffnet, bekommt die Gruppe automatisch in sein eigenes "Meine Gruppen" -
    # genau wie eine besuchte Profilseite in "Zuletzt gesehen" landet.
    return _meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon)


@app.route("/gruppe/<gruppe_id>/mitglied", methods=["POST"])
def gruppe_mitglied_hinzufuegen(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM gruppen WHERE id = %s;", (gruppe_id,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        abort(404)

    riot_id_input = request.form.get("riot_id", "").strip()
    if riot_id_input and "#" in riot_id_input:
        name, tag = riot_id_input.rsplit("#", 1)
        name, tag = name.strip(), tag.strip()
        try:
            puuid, _ = sync_player(cur, conn, headers, name, tag)
            cur.execute(
                "INSERT INTO gruppen_mitglieder (gruppe_id, puuid) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING;",
                (gruppe_id, puuid)
            )
            conn.commit()
        except SummonerNotFound:
            pass  # Stiller Fehlschlag - die Gruppe zeigt einfach weiterhin nur die gültigen Mitglieder

    cur.close()
    conn.close()
    return redirect(url_for("gruppe_ansehen", gruppe_id=gruppe_id))


@app.route("/gruppe/<gruppe_id>/mitglied/<puuid>/entfernen", methods=["POST"])
def gruppe_mitglied_entfernen(gruppe_id, puuid):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM gruppen_mitglieder WHERE gruppe_id = %s AND puuid = %s;", (gruppe_id, puuid)
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("gruppe_ansehen", gruppe_id=gruppe_id))


@app.route("/gruppe/<gruppe_id>/umbenennen", methods=["POST"])
def gruppe_umbenennen(gruppe_id):
    name = request.form.get("name", "").strip()
    icon = request.form.get("icon", "").strip()
    if icon not in GRUPPEN_ICONS:
        icon = None

    conn = get_connection()
    cur = conn.cursor()
    if name and icon:
        cur.execute("UPDATE gruppen SET name = %s, icon = %s WHERE id = %s;", (name, icon, gruppe_id))
    elif name:
        cur.execute("UPDATE gruppen SET name = %s WHERE id = %s;", (name, gruppe_id))
    conn.commit()
    cur.close()
    conn.close()
    # Das neue Cookie mit dem aktuellen Namen/Icon wird gleich beim Redirect auf
    # gruppe_ansehen() automatisch mitgeschrieben (die liest immer frisch aus der DB).
    return redirect(url_for("gruppe_ansehen", gruppe_id=gruppe_id))


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


@app.route("/profil")
def profil():
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
        # Kein Suchbegriff: auf das zuletzt von DIESEM Browser angesehene Profil zurückfallen
        # (statt eines global letzten Profils aus der DB - jeder Besucher soll nur sein
        # eigenes zuletzt gesehenes Profil sehen, nicht das irgendeines anderen Nutzers).
        zg = _lese_zuletzt_gesehen()
        if zg:
            puuid = zg[0]["puuid"]

    zuletzt_gesehen = _mit_farbe(_lese_zuletzt_gesehen())
    meine_gruppen = _lese_meine_gruppen()

    if puuid is None:
        cur.close()
        conn.close()
        return render_template(
            "dashboard.html",
            kein_spieler=True,
            fehler=fehler,
            riot_id_input=riot_id_input,
            zuletzt_gesehen=zuletzt_gesehen,
            meine_gruppen=meine_gruppen,
            gruppen_icons=GRUPPEN_ICONS,
        )

    cur.execute("SELECT riot_name, riot_tag FROM players WHERE puuid = %s;", (puuid,))
    row = cur.fetchone()
    if row is None:
        # Puuid aus dem Cookie/Fallback verweist auf keinen (mehr) existierenden Spieler
        # (z.B. nach einem DB-Reset) - sauber auf den leeren Zustand zurückfallen statt
        # beim Entpacken von None abzustürzen.
        cur.close()
        conn.close()
        return render_template(
            "dashboard.html",
            kein_spieler=True,
            fehler=fehler,
            riot_id_input=riot_id_input,
            zuletzt_gesehen=zuletzt_gesehen,
            meine_gruppen=meine_gruppen,
            gruppen_icons=GRUPPEN_ICONS,
        )
    riot_name, riot_tag = row

    # Solo/Duo und Flex getrennt anzeigen (nicht TFT, nicht die separaten "JADE_..."-o.ä.
    # Event-Warteschlangen, die Riot manchmal zusätzlich im selben Response mitschickt - siehe
    # get_player_ranks() für Details). Die Richtwert-Vergleiche unten bleiben bewusst an
    # Solo/Duo verankert (der übliche Elo-Referenzpunkt), Flex ist rein informativ.
    ranks = get_player_ranks(puuid, headers)
    solo_rang, flex_rang = ranks["solo"], ranks["flex"]
    anzeige_rang_solo = f"{solo_rang['tier']} {solo_rang['rank']}" if solo_rang else "Unranked"
    anzeige_rang_flex = f"{flex_rang['tier']} {flex_rang['rank']}" if flex_rang else "Unranked"
    tier = normalize_tier(solo_rang["tier"]) if solo_rang else "GOLD"

    cur.execute(MATCH_QUERY, (puuid, ANZAHL_MATCHES))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    ddragon_version = get_ddragon_version()
    profile_icon_id = get_summoner_icon_id(puuid, headers)

    # Highest-Mastery-Champion als transparenter Profil-Hintergrund, mit zufälligem Skin bei
    # jedem Seitenaufruf. Fällt auf den zuletzt gespielten Champion zurück, falls die Mastery-
    # API fehlschlägt oder (bei brandneuen Accounts) noch keine Mastery-Punkte existieren.
    mastery_champion_id = get_top_mastery_champion_id(puuid, headers)
    mastery_champion = get_champion_by_numeric_id(mastery_champion_id) if mastery_champion_id else None
    hero_champion = mastery_champion["key"] if mastery_champion else (rows[0][1] if rows else None)
    hero_splash = random_champion_splash_url(hero_champion) if hero_champion else None

    spiele = []
    alle_vergleiche = []
    siege = 0

    for row in rows:
        werte, vergleich = match_metrics(row, tier)
        alle_vergleiche.append(vergleich)
        werte["champion_icon"] = champion_icon_url(werte["champion"], ddragon_version)
        werte["champion_splash"] = champion_splash_url(werte["champion"])
        werte["ringe"] = ringe_fuer_match(vergleich)
        werte["note"] = berechne_note(vergleich)
        werte["note_klasse"] = f"note-{werte['note'][0].lower()}"
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
            "satz": vergleichssatz(KATEGORIEN[key]["label"], stats["avg_wert"], stats["avg_richtwert"], tier),
            "unter_anzahl": stats["unter_anzahl"],
            "total": stats["total"],
            "avg_wert": format_wert(key, stats["avg_wert"]),
            "avg_richtwert": format_wert(key, stats["avg_richtwert"]),
            "erreicht_prozent": round(stats["avg_wert"] / stats["avg_richtwert"] * 100) if stats["avg_richtwert"] else 0,
        }
        for key, stats in top_probleme(alle_vergleiche)
    ]

    staerken = [
        {
            "label": KATEGORIEN[key]["label"],
            "satz": vergleichssatz(KATEGORIEN[key]["label"], stats["avg_wert"], stats["avg_richtwert"], tier),
            "ueber_anzahl": stats["total"] - stats["unter_anzahl"],
            "total": stats["total"],
            "avg_wert": format_wert(key, stats["avg_wert"]),
            "avg_richtwert": format_wert(key, stats["avg_richtwert"]),
            "erreicht_prozent": round(stats["avg_wert"] / stats["avg_richtwert"] * 100) if stats["avg_richtwert"] else 0,
        }
        for key, stats in top_staerken(alle_vergleiche)
    ]

    # Chart-Daten: chronologisch (älteste zuerst), da die DB-Abfrage neueste zuerst liefert
    chronologisch = list(reversed(spiele))
    kda_chart = {
        "labels": [s["champion"] for s in chronologisch],
        "kda": [round(s["kda"], 2) for s in chronologisch],
        "farben": ["#34d399" if s["win"] else "#f76c8a" for s in chronologisch],
    }

    # Aktuelles Profil ganz nach vorne in die "Zuletzt gesehen"-Liste DIESES Browsers -
    # direkt fürs Rendern wiederverwendet, damit es sofort oben auftaucht statt erst beim
    # nächsten Request.
    neue_liste = _neue_zuletzt_gesehen_liste(puuid, riot_name, riot_tag)

    resp = make_response(render_template(
        "dashboard.html",
        kein_spieler=False,
        fehler=fehler,
        riot_id_input=riot_id_input,
        zuletzt_gesehen=_mit_farbe(neue_liste),
        meine_gruppen=meine_gruppen,
        gruppen_icons=GRUPPEN_ICONS,
        puuid=puuid,
        riot_name=riot_name,
        riot_tag=riot_tag,
        rang_solo=anzeige_rang_solo,
        rang_flex=anzeige_rang_flex,
        summoner_icon=summoner_icon_url(profile_icon_id, ddragon_version) if profile_icon_id else None,
        hero_splash=hero_splash,
        anzahl_spiele=len(spiele),
        siege=siege,
        niederlagen=len(spiele) - siege,
        spiele=spiele,
        probleme=probleme,
        staerken=staerken,
        kda_chart=kda_chart,
    ))
    return _cookie_setzen(resp, neue_liste)


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


if __name__ == "__main__":
    # Nur für die lokale Entwicklung - im Live-Betrieb läuft die App über Gunicorn
    # (siehe Procfile), das diesen Block nie ausführt. FLASK_DEBUG=0 als zusätzliche
    # Absicherung, falls die App doch mal versehentlich direkt gestartet wird - im Netz
    # darf debug NIE an sein (offener Remote-Debugger).
    # Port 5000 kollidiert auf macOS oft mit dem AirPlay-Receiver-Dienst.
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, port=port)
