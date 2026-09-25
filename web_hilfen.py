"""Gemeinsame Helfer mehrerer Seiten: "Zuletzt gesehen"/"Meine Gruppen" (Cookie bzw. Konto),
Avatare, Namenssuche und die Anmelde-Session."""
import json
import urllib.parse
from datetime import datetime

from flask import g, request, session

from app_core import LOGIN_AKTIV, headers
from db import get_connection
from riot_assets import get_ddragon_version, get_summoner_icon_id, summoner_icon_url


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


def lese_zuletzt_gesehen():
    """Zuletzt angesehene Profile: angemeldet aus der DB (geräteübergreifend), sonst aus dem
    Cookie DIESES Browsers. Jeder sieht nur seine eigene Liste. Pro Request nur einmal aus der
    DB gelesen (wird z.B. in profil() mehrfach gebraucht)."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return _cookie_zuletzt_gesehen()
    if "zuletzt_gesehen" not in g:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT pl.puuid, pl.riot_name, pl.riot_tag
            FROM nutzer_zuletzt_gesehen z JOIN players pl ON pl.puuid = z.puuid
            WHERE z.discord_id = %s
            ORDER BY z.zuletzt DESC
            LIMIT %s;
        """, (discord_id, MAX_ZULETZT_GESEHEN))
        g.zuletzt_gesehen = [{"puuid": p, "name": n, "tag": t} for p, n, t in cur.fetchall()]
        cur.close()
        conn.close()
    return list(g.zuletzt_gesehen)


def merke_zuletzt_gesehen(puuid):
    """Angemeldet: Profilaufruf zusätzlich in der DB merken (das Cookie schreibt profil())."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO nutzer_zuletzt_gesehen (discord_id, puuid) VALUES (%s, %s)
        ON CONFLICT (discord_id, puuid) DO UPDATE SET zuletzt = now();
    """, (discord_id, puuid))
    conn.commit()
    cur.close()
    conn.close()
    g.pop("zuletzt_gesehen", None)


def _cookie_zuletzt_gesehen():
    """Der Cookie-Wert ist selbst URL-kodiert (statt Werkzeug/dem Client das Quoting des rohen
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


def mit_farbe(eintraege):
    return [
        {**e, "farbe": AVATAR_FARBEN[sum(ord(c) for c in e["name"]) % len(AVATAR_FARBEN)]}
        for e in eintraege
    ]


def mit_summoner_icons(eintraege):
    """Hängt je Eintrag die URL des vom Spieler gewählten Profil-Icons an ("icon_url", sonst
    None -> Template fällt auf den Anfangsbuchstaben zurück). Die Icon-ID liegt in players;
    fehlt sie noch (Profil seit Einführung nicht mehr angesehen), wird sie einmalig bei Riot
    nachgeholt und gespeichert."""
    if not eintraege:
        return eintraege
    conn = get_connection()
    cur = conn.cursor()
    puuids = [e["puuid"] for e in eintraege]
    cur.execute("SELECT puuid, profile_icon_id FROM players WHERE puuid = ANY(%s);", (puuids,))
    icon_ids = dict(cur.fetchall())
    for puuid in puuids:
        if puuid in icon_ids and icon_ids[puuid] is None:
            try:
                icon_ids[puuid] = get_summoner_icon_id(puuid, headers)
            except Exception:
                continue
            if icon_ids[puuid] is not None:
                cur.execute("UPDATE players SET profile_icon_id = %s WHERE puuid = %s;", (icon_ids[puuid], puuid))
    conn.commit()
    cur.close()
    conn.close()

    version = get_ddragon_version()
    return [
        {**e, "icon_url": summoner_icon_url(icon_ids[e["puuid"]], version) if icon_ids.get(e["puuid"]) else None}
        for e in eintraege
    ]


def neue_zuletzt_gesehen_liste(puuid, riot_name, riot_tag):
    """Aktuell angesehenes Profil ganz nach vorne (dedupliziert per puuid), auf
    MAX_ZULETZT_GESEHEN begrenzt. Reine Berechnung (kein Response nötig), damit sich die neue
    Liste sowohl fürs Rendern als auch fürs Cookie-Schreiben wiederverwenden lässt."""
    bisherige = [e for e in lese_zuletzt_gesehen() if e["puuid"] != puuid]
    return ([{"puuid": puuid, "name": riot_name, "tag": riot_tag}] + bisherige)[:MAX_ZULETZT_GESEHEN]


def cookie_setzen(resp, liste):
    """Ein Jahr gültig, httponly (Server-only, kein JS-Zugriff nötig)."""
    resp.set_cookie(
        ZULETZT_GESEHEN_COOKIE, urllib.parse.quote(json.dumps(liste)),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
    )
    return resp


MEINE_GRUPPEN_COOKIE = "meine_gruppen"
MAX_MEINE_GRUPPEN = 20


def lese_meine_gruppen():
    """Schnellzugriff "Meine Gruppen". Angemeldet aus der DB: besuchte/erstellte Gruppen PLUS
    automatisch jede Gruppe, in der der verknüpfte Riot-Account Mitglied ist - Name und Icon
    immer aktuell. Nicht angemeldet: nur die Gruppen-Links, die DIESER Browser kennt (Cookie)."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return cookie_meine_gruppen()
    if "meine_gruppen" not in g:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT gr.id, gr.name, COALESCE(gr.icon, '🛡️'), MAX(t.zeit) AS zeit
            FROM (
                SELECT gruppe_id, zuletzt_besucht AS zeit FROM nutzer_gruppen WHERE discord_id = %s
                UNION ALL
                SELECT gm.gruppe_id, gm.hinzugefuegt_am
                FROM gruppen_mitglieder gm JOIN nutzer n ON n.riot_puuid = gm.puuid
                WHERE n.discord_id = %s
            ) t
            JOIN gruppen gr ON gr.id = t.gruppe_id
            GROUP BY gr.id, gr.name, gr.icon
            ORDER BY zeit DESC
            LIMIT %s;
        """, (discord_id, discord_id, MAX_MEINE_GRUPPEN))
        g.meine_gruppen = [{"id": i, "name": n, "icon": ic} for i, n, ic, _ in cur.fetchall()]
        cur.close()
        conn.close()
    return list(g.meine_gruppen)


def cookie_meine_gruppen():
    """Die Gruppe selbst lebt in der DB und ist über ihre ID für jeden mit dem Link sichtbar
    (kein Login nötig) - das Cookie merkt sich nur, welche Gruppen-Links DIESER Browser kennt."""
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


def meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon="🛡️"):
    discord_id = angemeldete_discord_id()
    if discord_id:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO nutzer_gruppen (discord_id, gruppe_id) VALUES (%s, %s)
            ON CONFLICT (discord_id, gruppe_id) DO UPDATE SET zuletzt_besucht = now();
        """, (discord_id, gruppe_id))
        conn.commit()
        cur.close()
        conn.close()
        g.pop("meine_gruppen", None)
    bisherige = [e for e in cookie_meine_gruppen() if e["id"] != gruppe_id]
    neu = ([{"id": gruppe_id, "name": name, "icon": icon}] + bisherige)[:MAX_MEINE_GRUPPEN]
    resp.set_cookie(
        MEINE_GRUPPEN_COOKIE, urllib.parse.quote(json.dumps(neu)),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
    )
    return resp


GRUPPEN_ICONS = ["🛡️", "⚔️", "🔥", "🐉", "👑", "🎯", "💀", "🏆", "⚡", "🌙", "🦂", "🩸"]


MAX_NAMENS_TREFFER = 25


def spieler_mit_namen(cur, name):
    """Alle bekannten Spieler, die exakt so heißen (ohne Tag, Groß-/Kleinschreibung egal) -
    getrackte Profile zuerst, sonst die zuletzt gesehenen."""
    cur.execute("""
        SELECT b.puuid, b.riot_name, b.riot_tag
        FROM bekannte_spieler b
        LEFT JOIN players pl ON pl.puuid = b.puuid AND NOT COALESCE(pl.is_meta_sample, FALSE)
        WHERE lower(b.riot_name) = lower(%s)
        ORDER BY (pl.puuid IS NOT NULL) DESC, b.zuletzt_gesehen DESC
        LIMIT %s;
    """, (name, MAX_NAMENS_TREFFER))
    return [{"puuid": p, "name": n, "tag": t} for p, n, t in cur.fetchall()]


def angemeldete_discord_id():
    nutzer = session.get("nutzer") if LOGIN_AKTIV else None
    return nutzer["id"] if nutzer else None


def angemeldeter_nutzer():
    """Session-Daten des angemeldeten Nutzers (id, name, avatar, riot, puuid) oder None.
    Sitzungen von vor der "puuid"-Erweiterung werden dabei einmalig aus der DB ergänzt."""
    if not angemeldete_discord_id():
        return None
    if "puuid" not in session["nutzer"]:
        conn = get_connection()
        cur = conn.cursor()
        session_riot_aktualisieren(cur, session["nutzer"]["id"])
        cur.close()
        conn.close()
    return session["nutzer"]


def session_riot_aktualisieren(cur, discord_id):
    cur.execute("""
        SELECT pl.puuid, pl.riot_name, pl.riot_tag FROM nutzer n JOIN players pl ON pl.puuid = n.riot_puuid
        WHERE n.discord_id = %s;
    """, (discord_id,))
    row = cur.fetchone()
    nutzer = dict(session["nutzer"])
    nutzer["puuid"] = row[0] if row else None
    nutzer["riot"] = f"{row[1]}#{row[2]}" if row else None
    session["nutzer"] = nutzer
    g.pop("meine_gruppen", None)  # Mitgliedschaften des Riot-Accounts zählen dort mit


def cookie_listen_uebernehmen(cur, discord_id):
    """Beim Login: was dieser Browser bisher (ohne Konto) gesammelt hat, ins Konto übernehmen -
    Reihenfolge bleibt erhalten, ältere Einträge bekommen etwas frühere Zeitstempel."""
    for i, e in enumerate(_cookie_zuletzt_gesehen()):
        cur.execute("""
            INSERT INTO nutzer_zuletzt_gesehen (discord_id, puuid, zuletzt)
            SELECT %s, puuid, now() - %s * interval '1 second' FROM players WHERE puuid = %s
            ON CONFLICT (discord_id, puuid) DO NOTHING;
        """, (discord_id, i + 1, e["puuid"]))
    for i, e in enumerate(cookie_meine_gruppen()):
        cur.execute("""
            INSERT INTO nutzer_gruppen (discord_id, gruppe_id, zuletzt_besucht)
            SELECT %s, id, now() - %s * interval '1 second' FROM gruppen WHERE id = %s
            ON CONFLICT (discord_id, gruppe_id) DO NOTHING;
        """, (discord_id, i + 1, e["id"]))
