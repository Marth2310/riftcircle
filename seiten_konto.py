"""Discord-Login und Konto-Seite mit Riot-Verknüpfung per Icon-Bestätigung."""
import random
import secrets

from flask import abort, redirect, render_template, request, session, url_for

from app_core import (
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    LOGIN_AKTIV,
    PUBLIC_BASE_URL,
    app,
    headers,
)
from db import get_connection
from discord_login import hole_discord_nutzer, login_url
from riot_assets import get_ddragon_version, get_summoner_icon_id, summoner_icon_url
from riot_fetch import RiotApiNichtVerfuegbar, SummonerNotFound, finde_riot_account, sync_player
from web_hilfen import (
    angemeldete_discord_id,
    cookie_listen_uebernehmen,
    lese_meine_gruppen,
    session_riot_aktualisieren,
    spieler_mit_namen,
)


VERIFIZIERUNG_MINUTEN = 30
# Die Standard-Icons 0-28 besitzt jeder Account - nur die taugen für die Bestätigung
STANDARD_ICONS = range(0, 29)

KONTO_MELDUNGEN = {
    "login_abgebrochen": ("fehler", "Die Anmeldung wurde abgebrochen."),
    "login_fehler": ("fehler", "Discord hat die Anmeldung nicht bestätigt - bitte nochmal versuchen."),
    "angemeldet": ("ok", "Du bist mit Discord angemeldet."),
    "riot_ungueltig": ("fehler", "Bitte gib deine Riot-ID als Name#Tag ein."),
    "riot_unbekannt": ("fehler", "Diesen Riot-Account gibt es nicht - Schreibweise und Tag prüfen."),
    "riot_nicht_euw": ("fehler", "Zu diesem Account gibt es keinen EUW-Summoner. RiftCircle unterstützt aktuell nur EUW."),
    "icon_falsch": ("fehler", "Dein Profil-Icon ist noch nicht das angezeigte. Im Client ändern, kurz warten und nochmal prüfen."),
    "abgelaufen": ("fehler", f"Die Bestätigung ist abgelaufen (nach {VERIFIZIERUNG_MINUTEN} Minuten) - bitte neu starten."),
    "keine_spiele": ("fehler", "Der Account ist bestätigt, aber es wurden keine EUW-Spiele gefunden."),
    "verknuepft": ("ok", "Riot-Account bestätigt und verknüpft! Du kannst dein Profil-Icon jetzt wieder zurückstellen."),
    "riot_api": ("fehler", "Riot nimmt gerade keine Anfragen von RiftCircle an (API-Key abgelaufen, Rate-Limit oder "
                           "Störung) - dein Account ist nicht das Problem. Bitte später nochmal versuchen."),
    "getrennt": ("ok", "Verknüpfung gelöst."),
    "gespeichert": ("ok", "Einstellung gespeichert."),
}


def _discord_redirect_uri():
    """Muss exakt einer der im Discord Developer Portal eingetragenen Adressen entsprechen."""
    if request.host.split(":")[0] in ("localhost", "127.0.0.1"):
        return f"http://{request.host}/auth/discord/callback"
    return PUBLIC_BASE_URL + "/auth/discord/callback"


def _sicheres_ziel(ziel):
    """Nur Pfade auf dieser Seite - sonst ließe sich der Login als Weiterleitung auf fremde
    Seiten missbrauchen (…?weiter=https://phishing.example)."""
    if ziel and ziel.startswith("/") and not ziel.startswith("//") and "\\" not in ziel:
        return ziel
    return url_for("konto")


@app.route("/auth/discord/login")
def discord_login():
    if not LOGIN_AKTIV:
        abort(404)
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["login_weiter"] = _sicheres_ziel(request.args.get("weiter"))
    return redirect(login_url(DISCORD_CLIENT_ID, _discord_redirect_uri(), state))


@app.route("/auth/discord/callback")
def discord_callback():
    if not LOGIN_AKTIV:
        abort(404)
    erwartet = session.pop("oauth_state", None)
    weiter = session.pop("login_weiter", None) or url_for("konto")
    # state schützt davor, dass jemand anderes einem seinen eigenen Login "unterschiebt"
    if request.args.get("error") or not erwartet or not secrets.compare_digest(request.args.get("state", ""), erwartet):
        return redirect(url_for("konto", meldung="login_abgebrochen"))

    daten = hole_discord_nutzer(
        DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, request.args.get("code", ""), _discord_redirect_uri()
    )
    if not daten:
        return redirect(url_for("konto", meldung="login_fehler"))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO nutzer (discord_id, discord_name, discord_avatar) VALUES (%s, %s, %s)
        ON CONFLICT (discord_id) DO UPDATE SET
            discord_name = EXCLUDED.discord_name, discord_avatar = EXCLUDED.discord_avatar, zuletzt_login = now();
    """, (daten["id"], daten["name"], daten["avatar"]))
    cookie_listen_uebernehmen(cur, daten["id"])
    conn.commit()
    session.permanent = True
    session["nutzer"] = {"id": daten["id"], "name": daten["name"], "avatar": daten["avatar"], "riot": None}
    session_riot_aktualisieren(cur, daten["id"])
    cur.close()
    conn.close()
    return redirect(weiter)


@app.route("/auth/logout", methods=["POST"])
def discord_logout():
    session.pop("nutzer", None)
    return redirect(url_for("landing"))


@app.route("/konto")
def konto():
    if not LOGIN_AKTIV:
        abort(404)
    meldung = KONTO_MELDUNGEN.get(request.args.get("meldung", ""))
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return render_template("konto.html", nutzer=None, meldung=meldung)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT n.discord_name, n.discord_avatar, n.riot_puuid, pl.riot_name, pl.riot_tag, pl.profile_icon_id,
               n.verknuepft_am, n.pruef_name, n.pruef_tag, n.pruef_icon,
               n.pruef_seit IS NOT NULL AND n.pruef_seit > now() - %s * interval '1 minute',
               COALESCE(n.discord_erwaehnen, TRUE)
        FROM nutzer n LEFT JOIN players pl ON pl.puuid = n.riot_puuid
        WHERE n.discord_id = %s;
    """, (VERIFIZIERUNG_MINUTEN, discord_id))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        # Konto existiert nicht mehr (z.B. DB zurückgesetzt) - sauber abmelden
        session.pop("nutzer", None)
        return render_template("konto.html", nutzer=None, meldung=meldung)

    (name, avatar, riot_puuid, riot_name, riot_tag, icon_id, verknuepft_am,
     pruef_name, pruef_tag, pruef_icon, pruef_aktiv, erwaehnen) = row
    version = get_ddragon_version()
    return render_template(
        "konto.html", meldung=meldung,
        nutzer={"name": name, "avatar": avatar, "erwaehnen": erwaehnen},
        meine_gruppen=lese_meine_gruppen(),
        riot={
            "name": riot_name, "tag": riot_tag,
            "icon": summoner_icon_url(icon_id, version) if icon_id is not None else None,
            "seit": verknuepft_am.strftime("%d.%m.%Y") if verknuepft_am else None,
        } if riot_puuid else None,
        pruefung={
            "name": pruef_name, "tag": pruef_tag, "icon_id": pruef_icon,
            "icon": summoner_icon_url(pruef_icon, version),
        } if pruef_aktiv and not riot_puuid else None,
    )


@app.route("/konto/verknuepfen", methods=["POST"])
def konto_verknuepfen():
    """Schritt 1: Riot-Account auswählen und ein zufälliges Standard-Icon vorgeben, das der
    Spieler kurz im Client setzen muss - nur der echte Besitzer kann sein Icon ändern."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return redirect(url_for("konto"))
    eingabe = request.form.get("riot_id", "").strip()

    conn = get_connection()
    cur = conn.cursor()
    if eingabe and "#" not in eingabe:
        treffer = spieler_mit_namen(cur, eingabe)
        if len(treffer) == 1:
            eingabe = f"{treffer[0]['name']}#{treffer[0]['tag']}"
    if "#" not in eingabe:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_ungueltig"))

    name, tag = (teil.strip() for teil in eingabe.rsplit("#", 1))
    try:
        account = finde_riot_account(headers, name, tag)
    except RiotApiNichtVerfuegbar:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_api"))
    if not account:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_unbekannt"))
    try:
        aktuelles_icon = get_summoner_icon_id(account["puuid"], headers)
    except Exception:
        aktuelles_icon = None
    if aktuelles_icon is None:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_nicht_euw"))

    ziel_icon = random.choice([i for i in STANDARD_ICONS if i != aktuelles_icon])
    cur.execute("""
        UPDATE nutzer SET pruef_puuid = %s, pruef_name = %s, pruef_tag = %s, pruef_icon = %s, pruef_seit = now()
        WHERE discord_id = %s;
    """, (account["puuid"], account["name"], account["tag"], ziel_icon, discord_id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("konto"))


@app.route("/konto/pruefen", methods=["POST"])
def konto_pruefen():
    """Schritt 2: Ist das vorgegebene Icon jetzt gesetzt, gehört der Account dem Nutzer."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return redirect(url_for("konto"))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT pruef_puuid, pruef_name, pruef_tag, pruef_icon,
               pruef_seit > now() - %s * interval '1 minute'
        FROM nutzer WHERE discord_id = %s AND pruef_puuid IS NOT NULL;
    """, (VERIFIZIERUNG_MINUTEN, discord_id))
    row = cur.fetchone()
    if row is None or not row[4]:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="abgelaufen"))
    puuid, name, tag, ziel_icon, _ = row

    try:
        aktuelles_icon = get_summoner_icon_id(puuid, headers)
    except Exception:
        aktuelles_icon = None
    if aktuelles_icon is None:
        # Der Account wurde in Schritt 1 schon gefunden - kein Icon heißt hier: Riot antwortet nicht
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_api"))
    if aktuelles_icon != ziel_icon:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="icon_falsch"))

    # Bestätigt. Spieler synchronisieren (legt ihn in players an, nötig für die Verknüpfung)
    try:
        sync_player(cur, conn, headers, name, tag)
    except RiotApiNichtVerfuegbar:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="riot_api"))
    except SummonerNotFound:
        cur.close()
        conn.close()
        return redirect(url_for("konto", meldung="keine_spiele"))
    # Wer den Account bestätigt, ist der Besitzer - eine ältere Verknüpfung mit einem
    # anderen Konto wird dabei gelöst
    cur.execute("UPDATE nutzer SET riot_puuid = NULL WHERE riot_puuid = %s AND discord_id <> %s;", (puuid, discord_id))
    cur.execute("""
        UPDATE nutzer SET riot_puuid = %s, verknuepft_am = now(),
               pruef_puuid = NULL, pruef_name = NULL, pruef_tag = NULL, pruef_icon = NULL, pruef_seit = NULL
        WHERE discord_id = %s;
    """, (puuid, discord_id))
    cur.execute("UPDATE players SET profile_icon_id = %s WHERE puuid = %s;", (aktuelles_icon, puuid))
    conn.commit()
    session_riot_aktualisieren(cur, discord_id)
    cur.close()
    conn.close()
    return redirect(url_for("konto", meldung="verknuepft"))


@app.route("/konto/einstellungen", methods=["POST"])
def konto_einstellungen():
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return redirect(url_for("konto"))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE nutzer SET discord_erwaehnen = %s WHERE discord_id = %s;",
        (request.form.get("erwaehnen") == "1", discord_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for("konto", meldung="gespeichert"))


@app.route("/konto/trennen", methods=["POST"])
def konto_trennen():
    """Löst die Verknüpfung bzw. bricht eine laufende Bestätigung ab."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return redirect(url_for("konto"))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE nutzer SET riot_puuid = NULL, verknuepft_am = NULL,
               pruef_puuid = NULL, pruef_name = NULL, pruef_tag = NULL, pruef_icon = NULL, pruef_seit = NULL
        WHERE discord_id = %s;
    """, (discord_id,))
    conn.commit()
    session_riot_aktualisieren(cur, discord_id)
    cur.close()
    conn.close()
    return redirect(url_for("konto", meldung="getrennt"))
