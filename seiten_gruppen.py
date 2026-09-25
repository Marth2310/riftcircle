"""Gruppen: Feed, Wochen-Rangliste, Mitgliederverwaltung und Discord-Webhook."""
import json
import secrets
import urllib.parse

from flask import abort, make_response, redirect, render_template, request, url_for

from achievements import bestes_achievement
from app_core import PUBLIC_BASE_URL, app, headers
from db import get_connection
from discord_webhook import (
    MAX_EMBEDS_PRO_NACHRICHT,
    achievement_embed,
    erwaehnung,
    ist_gueltige_webhook_url,
    sende,
    test_embed,
    verbunden_embed,
    wochen_embed,
)
from riot_assets import champion_icon_url, champion_splash_url, get_ddragon_version
from riot_fetch import SummonerNotFound, sync_player
from web_hilfen import (
    GRUPPEN_ICONS,
    MEINE_GRUPPEN_COOKIE,
    angemeldeter_nutzer,
    cookie_meine_gruppen,
    lese_zuletzt_gesehen,
    meine_gruppen_cookie_setzen,
    mit_farbe,
    mit_summoner_icons,
    spieler_mit_namen,
    relative_zeit,
)
from wochenrueckblick import berechne_score


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
    return meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon)


GRUPPEN_FEED_LIMIT = 40

# Anzeige-Reihenfolge/Labels für den Rollen-Filter in der Gruppen-Ansicht
ROLLEN_LISTE = [
    ("TOP", "Top"), ("JUNGLE", "Jungle"), ("MIDDLE", "Mid"),
    ("BOTTOM", "Bot"), ("UTILITY", "Support"),
]
ROLLEN_LABEL = dict(ROLLEN_LISTE)
# "Zum ersten Mal X gespielt" erst ab so vielen gespeicherten Spielen davor - bei neu
# getrackten Spielern wäre sonst praktisch jedes Spiel ein "erstes Mal"
MIN_HISTORIE_NEUER_CHAMPION = 20


def _historie_merkmale(cur, puuids):
    """(match_id, puuid) -> {"siegesserie": n, "erstes_mal": bool} aus der kompletten
    gespeicherten Historie der Spieler. Die Serie zählt gespeicherte Spiele - fehlen
    dazwischen welche (mehr als 20 Spiele zwischen zwei Profil-Aufrufen), ist sie ungenau."""
    cur.execute("""
        SELECT p.puuid, p.match_id, p.champion, p.win
        FROM participants p JOIN matches m ON m.match_id = p.match_id
        WHERE p.puuid = ANY(%s)
        ORDER BY p.puuid, m.played_at, p.match_id;
    """, (list(puuids),))
    merkmale = {}
    aktueller, serie, gesehen, anzahl = None, 0, set(), 0
    for puuid, match_id, champion, win in cur.fetchall():
        if puuid != aktueller:
            aktueller, serie, gesehen, anzahl = puuid, 0, set(), 0
        serie = serie + 1 if win else 0
        merkmale[(match_id, puuid)] = {
            "siegesserie": serie,
            "erstes_mal": champion not in gesehen and anzahl >= MIN_HISTORIE_NEUER_CHAMPION,
        }
        gesehen.add(champion)
        anzahl += 1
    return merkmale


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
               p.penta_kills, p.quadra_kills, p.triple_kills, p.double_kills, p.inhibitoren_verloren,
               m.played_at, m.duration_seconds
        FROM participants p
        JOIN matches m ON p.match_id = m.match_id
        JOIN players pl ON p.puuid = pl.puuid
        WHERE p.puuid = ANY(%s)
        ORDER BY m.played_at DESC
        LIMIT %s;
    """, (mitglied_puuids, GRUPPEN_FEED_LIMIT))
    zeilen = cur.fetchall()
    historie = _historie_merkmale(cur, {row[1] for row in zeilen})

    feed = []
    for row in zeilen:
        (match_id, puuid, riot_name, riot_tag, champion, role, win, kills, deaths, assists,
         cs, damage_dealt, damage_rank, objectives_stolen, solo_kills, penta, quadra, triple, double,
         inhibitoren_verloren, played_at, duration_seconds) = row

        achievement_zeile = {
            "champion": champion, "win": win, "kills": kills, "deaths": deaths, "assists": assists,
            "damage_dealt": damage_dealt or 0, "damage_rank": damage_rank,
            "objectives_stolen": objectives_stolen or 0,
            "solo_kills": solo_kills or 0, "penta_kills": penta or 0, "quadra_kills": quadra or 0,
            "triple_kills": triple or 0, "double_kills": double or 0,
            "inhibitoren_verloren": inhibitoren_verloren,
            **historie.get((match_id, puuid), {}),
        }

        kda = (kills + assists) / deaths if deaths > 0 else (kills + assists)

        feed.append({
            "match_id": match_id, "puuid": puuid, "riot_name": riot_name, "riot_tag": riot_tag,
            "champion": champion, "champion_icon": champion_icon_url(champion, ddragon_version),
            "champion_splash": champion_splash_url(champion),
            "role": role, "role_label": ROLLEN_LABEL.get(role, "ARAM"),
            "win": win, "kills": kills, "deaths": deaths, "assists": assists,
            "kda": round(kda, 2), "cs": cs, "damage_dealt": damage_dealt,
            "played_at": played_at,
            "zeit_text": relative_zeit(played_at), "dauer_min": duration_seconds // 60,
            "achievement": bestes_achievement(achievement_zeile),
        })
    return feed


# Zeitraum -> SQL-Bedingung (feste Texte, keine Nutzereingabe) und ob Multikills pro Spiel zählen
RANGLISTEN_ZEITRAEUME = {
    # Anzeige auf der Gruppenseite: gleitend die letzten 7 Tage
    "woche": ("m.played_at >= now() - interval '7 days'", False),
    # Discord-Wochensieger: die zuletzt abgeschlossene Kalenderwoche (Mo 00:00 bis Mo 00:00)
    "letzte_kalenderwoche": (
        "m.played_at >= date_trunc('week', now()) - interval '7 days' AND m.played_at < date_trunc('week', now())",
        False,
    ),
    "monat": ("m.played_at >= date_trunc('month', now())", True),
    "gesamt": ("TRUE", True),
}
VERLAUF_WOCHEN = 8


def baue_rangliste(cur, mitglieder, zeitraum="woche"):
    """Wer war im Zeitraum am besten? Siehe wochenrueckblick.py für die Gewichtung. Leere
    Liste, falls in der Gruppe in dem Zeitraum noch niemand gespielt hat."""
    if not mitglieder:
        return []
    bedingung, multikills_pro_spiel = RANGLISTEN_ZEITRAEUME[zeitraum]

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
        WHERE p.puuid = ANY(%s) AND """ + bedingung + """
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
            "score": berechne_score(stats, multikills_pro_spiel),
        })
    rangliste.sort(key=lambda r: r["score"], reverse=True)
    return rangliste


def baue_verlauf(cur, mitglieder):
    """Winrate, Ø-KDA und Spielanzahl je Mitglied pro Kalenderwoche der letzten VERLAUF_WOCHEN
    Wochen (für das Liniendiagramm). Wochen ohne Spiele sind None (Lücke statt 0% Winrate).
    Nur Mitglieder mit mindestens einem Spiel im Zeitraum."""
    if not mitglieder:
        return None
    cur.execute("""
        SELECT generate_series(
            date_trunc('week', now()) - %s * interval '1 week', date_trunc('week', now()), interval '1 week'
        )::date;
    """, (VERLAUF_WOCHEN - 1,))
    wochen = [row[0] for row in cur.fetchall()]
    cur.execute("""
        SELECT p.puuid, date_trunc('week', m.played_at)::date,
               COUNT(*), SUM(CASE WHEN p.win THEN 1 ELSE 0 END), SUM(p.kills), SUM(p.deaths), SUM(p.assists)
        FROM participants p JOIN matches m ON m.match_id = p.match_id
        WHERE p.puuid = ANY(%s) AND m.played_at >= date_trunc('week', now()) - %s * interval '1 week'
        GROUP BY 1, 2;
    """, ([m["puuid"] for m in mitglieder], VERLAUF_WOCHEN - 1))
    werte = {(puuid, woche): (spiele, siege, k, d, a) for puuid, woche, spiele, siege, k, d, a in cur.fetchall()}

    reihen = []
    for m in mitglieder:
        eintraege = [werte.get((m["puuid"], w)) for w in wochen]
        if not any(eintraege):
            continue
        reihen.append({
            "name": m["name"],
            "farbe": m["farbe"],
            "winrate": [round(e[1] / e[0] * 100) if e else None for e in eintraege],
            "kda": [round((e[2] + e[4]) / e[3], 2) if e and e[3] else (float(e[2] + e[4]) if e else None)
                    for e in eintraege],
            "spiele": [e[0] if e else 0 for e in eintraege],
        })
    if not reihen:
        return None
    return {"labels": [f"KW {w.isocalendar()[1]}" for w in wochen], "reihen": reihen}


def _discord_beanspruchen(cur, conn, gruppe_id, typ, schluessel):
    """True, wenn dieser Post noch aussteht und jetzt von DIESEM Request übernommen wurde -
    der Eintrag wird VOR dem Senden committed, damit ein paralleler Request (2 Gunicorn-
    Worker) denselben Post nicht nochmal schickt."""
    cur.execute("""
        INSERT INTO discord_posts (gruppe_id, typ, schluessel) VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING RETURNING 1;
    """, (gruppe_id, typ, schluessel))
    uebernommen = cur.fetchone() is not None
    conn.commit()
    return uebernommen


def _discord_freigeben(cur, conn, gruppe_id, typ, schluessel):
    """Senden fehlgeschlagen -> wieder freigeben, damit der nächste Auslöser es erneut versucht."""
    cur.execute(
        "DELETE FROM discord_posts WHERE gruppe_id = %s AND typ = %s AND schluessel = ANY(%s);",
        (gruppe_id, typ, list(schluessel)),
    )
    conn.commit()


def _discord_ids_zum_erwaehnen(cur, puuids):
    """puuid -> Discord-ID für verknüpfte Spieler, die Erwähnungen nicht abgeschaltet haben."""
    if not puuids:
        return {}
    cur.execute("""
        SELECT riot_puuid, discord_id FROM nutzer
        WHERE riot_puuid = ANY(%s) AND COALESCE(discord_erwaehnen, TRUE);
    """, (list(puuids),))
    return dict(cur.fetchall())


def _discord_senden(cur, conn, gruppe_id, webhook, embeds, typ, schluessel, erwaehnte_ids=(), anlass=""):
    """Sendet und räumt bei Fehlern auf. False = abbrechen (nichts weiter senden)."""
    status = sende(webhook, embeds, PUBLIC_BASE_URL, erwaehnung(erwaehnte_ids, anlass), erwaehnte_ids)
    if 200 <= status < 300:
        return True
    _discord_freigeben(cur, conn, gruppe_id, typ, schluessel)
    if status in (401, 404):
        # Webhook wurde in Discord gelöscht - Verbindung trennen statt es ewig weiter zu versuchen
        cur.execute("UPDATE gruppen SET discord_webhook = NULL WHERE id = %s;", (gruppe_id,))
        conn.commit()
    return False


def benachrichtige_gruppe(cur, conn, gruppe_id, feed=None, mitglieder=None):
    """Postet ausstehende Achievements (nur aus Spielen seit dem Verbinden) und den Sieger der
    zuletzt abgeschlossenen Kalenderwoche in den Discord-Kanal der Gruppe. Läuft (bis zum Riot-
    Production-Key) nur, wenn etwas auf der Seite passiert - Profil-Suche, Mitglied hinzufügen,
    Gruppe öffnen -, es gibt keinen Hintergrund-Job. feed/mitglieder können übergeben werden,
    wenn der Aufrufer sie ohnehin schon berechnet hat."""
    cur.execute(
        "SELECT name, icon, discord_webhook, discord_seit FROM gruppen WHERE id = %s;", (gruppe_id,)
    )
    row = cur.fetchone()
    if not row or not row[2]:
        return
    name, icon, webhook, seit = row
    icon = icon or "🛡️"
    gruppen_url = PUBLIC_BASE_URL + url_for("gruppe_ansehen", gruppe_id=gruppe_id)

    if mitglieder is None:
        cur.execute("""
            SELECT pl.puuid, pl.riot_name, pl.riot_tag
            FROM gruppen_mitglieder gm JOIN players pl ON gm.puuid = pl.puuid
            WHERE gm.gruppe_id = %s ORDER BY gm.hinzugefuegt_am;
        """, (gruppe_id,))
        mitglieder = mit_farbe([{"puuid": p, "name": n, "tag": t} for p, n, t in cur.fetchall()])
    if feed is None:
        feed = baue_gruppen_feed(cur, [m["puuid"] for m in mitglieder])

    neue = sorted(
        (f for f in feed if f["achievement"] and f["played_at"] and seit and f["played_at"] >= seit),
        key=lambda f: f["played_at"],
    )
    offen = [
        f for f in neue
        if _discord_beanspruchen(cur, conn, gruppe_id, "achievement", f"{f['match_id']}:{f['puuid']}")
    ]
    discord_ids = _discord_ids_zum_erwaehnen(cur, {f["puuid"] for f in offen} | {m["puuid"] for m in mitglieder})
    for i in range(0, len(offen), MAX_EMBEDS_PRO_NACHRICHT):
        teil = offen[i:i + MAX_EMBEDS_PRO_NACHRICHT]
        embeds = [
            achievement_embed(
                f, name, icon,
                PUBLIC_BASE_URL + url_for("match_detail", match_id=f["match_id"], puuid=f["puuid"]),
            )
            for f in teil
        ]
        # Jeden verknüpften Spieler einmal pro Nachricht erwähnen, in Reihenfolge der Achievements
        erwaehnte = list(dict.fromkeys(discord_ids[f["puuid"]] for f in teil if f["puuid"] in discord_ids))
        if not _discord_senden(cur, conn, gruppe_id, webhook, embeds, "achievement",
                               [f"{f['match_id']}:{f['puuid']}" for f in offen[i:]],
                               erwaehnte, "🎉 Neues Achievement für"):
            return

    # Wochensieger der zuletzt abgeschlossenen Kalenderwoche - nur Wochen, die nach dem
    # Verbinden geendet haben (sonst käme beim Einrichten sofort der Sieger der Vorwoche)
    cur.execute("""
        SELECT to_char(date_trunc('week', now()) - interval '7 days', 'IYYY-"KW"IW'),
               to_char(date_trunc('week', now()) - interval '7 days', 'IW'),
               date_trunc('week', now()) > %s;
    """, (seit,))
    woche_key, kw, nach_verbinden = cur.fetchone()
    if nach_verbinden:
        rangliste = baue_rangliste(cur, mitglieder, "letzte_kalenderwoche")
        if rangliste and _discord_beanspruchen(cur, conn, gruppe_id, "woche", woche_key):
            sieger_id = discord_ids.get(rangliste[0]["puuid"])
            _discord_senden(
                cur, conn, gruppe_id, webhook,
                [wochen_embed(rangliste, f"KW {int(kw)}", name, icon, gruppen_url)],
                "woche", [woche_key],
                [sieger_id] if sieger_id else [], "👑 Glückwunsch zum Wochensieg,",
            )


def benachrichtige_gruppe_sicher(cur, conn, gruppe_id, **kwargs):
    """Discord ist Beiwerk - ein Fehler dort darf nie die eigentliche Seite kaputt machen."""
    try:
        benachrichtige_gruppe(cur, conn, gruppe_id, **kwargs)
    except Exception as e:
        conn.rollback()
        print(f"Discord-Benachrichtigung für Gruppe {gruppe_id} fehlgeschlagen: {e!r}")


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
    mitglieder = mit_farbe([{"puuid": p, "name": n, "tag": t} for p, n, t in mitglieder_rows])

    feed = baue_gruppen_feed(cur, [p for p, _, _ in mitglieder_rows])
    achievement_feed = [f for f in feed if f["achievement"]]
    ranglisten = {z: baue_rangliste(cur, mitglieder, z) for z in ("woche", "monat", "gesamt")}
    verlauf = baue_verlauf(cur, mitglieder)

    benachrichtige_gruppe_sicher(cur, conn, gruppe_id, feed=feed, mitglieder=mitglieder)
    # Erst NACH dem Benachrichtigen lesen - ein in Discord gelöschter Webhook wird dabei getrennt
    cur.execute("SELECT discord_webhook IS NOT NULL, discord_seit FROM gruppen WHERE id = %s;", (gruppe_id,))
    discord_verbunden, discord_seit = cur.fetchone()
    discord_meldung = DISCORD_MELDUNGEN.get(request.args.get("discord", ""))

    # "Zuletzt aktiv" je Mitglied - der Feed ist schon chronologisch (neuestes zuerst), der
    # erste Treffer pro puuid ist also automatisch deren letztes Spiel.
    zuletzt_aktiv = {}
    for f in feed:
        if f["puuid"] not in zuletzt_aktiv:
            zuletzt_aktiv[f["puuid"]] = f["zeit_text"]
    # Discord-Verknüpfungen der Mitglieder ("Das bist du" + Discord-Avatar am Mitglied)
    cur.execute(
        "SELECT riot_puuid, discord_name, discord_avatar FROM nutzer WHERE riot_puuid = ANY(%s);",
        ([m["puuid"] for m in mitglieder],),
    )
    discord_je_puuid = {p: {"name": n, "avatar": a} for p, n, a in cur.fetchall()}
    for m in mitglieder:
        m["zuletzt_aktiv"] = zuletzt_aktiv.get(m["puuid"])
        m["discord"] = discord_je_puuid.get(m["puuid"])
    mitglieder = mit_summoner_icons(mitglieder)
    ich = angemeldeter_nutzer()
    meine_puuid = ich.get("puuid") if ich else None

    # Schnellauswahl beim Mitglied-Hinzufügen: eigene "Zuletzt gesehen"-Profile, die noch
    # nicht in der Gruppe sind.
    mitglied_puuids = {m["puuid"] for m in mitglieder}
    vorschlaege = [e for e in mit_farbe(lese_zuletzt_gesehen()) if e["puuid"] not in mitglied_puuids]

    cur.close()
    conn.close()

    resp = make_response(render_template(
        "gruppe.html", gruppe_id=gruppe_id, gruppe_name=name, gruppe_icon=icon, mitglieder=mitglieder,
        feed=feed, achievement_feed=achievement_feed, ranglisten=ranglisten, verlauf=verlauf,
        gruppen_icons=GRUPPEN_ICONS, vorschlaege=vorschlaege, rollen_liste=ROLLEN_LISTE,
        discord_verbunden=discord_verbunden,
        discord_seit=discord_seit.strftime("%d.%m.%Y") if discord_seit else None,
        discord_meldung=discord_meldung,
        ich=ich, meine_puuid=meine_puuid,
        bin_mitglied=bool(meine_puuid) and meine_puuid in mitglied_puuids,
    ))
    # Wer den Link öffnet, bekommt die Gruppe automatisch in sein eigenes "Meine Gruppen" -
    # genau wie eine besuchte Profilseite in "Zuletzt gesehen" landet.
    return meine_gruppen_cookie_setzen(resp, gruppe_id, name, icon)


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
    if riot_id_input and "#" not in riot_id_input:
        # Name ohne Tag: nur übernehmen, wenn er im Namens-Index eindeutig ist
        treffer = spieler_mit_namen(cur, riot_id_input)
        if len(treffer) == 1:
            riot_id_input = f"{treffer[0]['name']}#{treffer[0]['tag']}"
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


@app.route("/gruppe/<gruppe_id>/beitreten", methods=["POST"])
def gruppe_beitreten(gruppe_id):
    """Ein Klick: den eigenen, per Discord verknüpften Riot-Account zur Gruppe hinzufügen."""
    ich = angemeldeter_nutzer()
    if not ich or not ich.get("puuid"):
        return redirect(url_for("konto"))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM gruppen WHERE id = %s;", (gruppe_id,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        abort(404)
    cur.execute(
        "INSERT INTO gruppen_mitglieder (gruppe_id, puuid) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
        (gruppe_id, ich["puuid"]),
    )
    conn.commit()
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


@app.route("/gruppe/<gruppe_id>/loeschen", methods=["POST"])
def gruppe_loeschen(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    # gruppen_mitglieder hat ON DELETE CASCADE auf gruppe_id - läuft automatisch mit.
    cur.execute("DELETE FROM gruppen WHERE id = %s;", (gruppe_id,))
    conn.commit()
    cur.close()
    conn.close()

    resp = make_response(redirect(url_for("landing")))
    # Auch aus dem eigenen "Meine Gruppen"-Cookie entfernen, sonst bleibt ein toter Link stehen.
    verbleibend = [e for e in cookie_meine_gruppen() if e["id"] != gruppe_id]
    resp.set_cookie(
        MEINE_GRUPPEN_COOKIE, urllib.parse.quote(json.dumps(verbleibend)),
        max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax",
    )
    return resp


DISCORD_MELDUNGEN = {
    "verbunden": ("ok", "Discord ist verbunden - schau in deinen Kanal, dort sollte gerade eine Nachricht angekommen sein."),
    "ungueltig": ("fehler", "Das ist keine Discord-Webhook-URL. Sie beginnt mit https://discord.com/api/webhooks/..."),
    "nicht_erreichbar": ("fehler", "Discord hat die Nachricht abgelehnt - ist der Webhook noch aktiv? Bitte URL prüfen."),
    "test_ok": ("ok", "Testnachricht wurde gesendet."),
    "test_fehler": ("fehler", "Testnachricht konnte nicht gesendet werden - der Webhook wurde evtl. in Discord gelöscht."),
    "getrennt": ("ok", "Discord wurde getrennt."),
}


def _gruppe_oder_404(cur, conn, gruppe_id):
    cur.execute("SELECT name, icon, discord_webhook FROM gruppen WHERE id = %s;", (gruppe_id,))
    row = cur.fetchone()
    if row is None:
        cur.close()
        conn.close()
        abort(404)
    return row[0], row[1] or "🛡️", row[2]


def _zur_gruppe(gruppe_id, meldung):
    return redirect(url_for("gruppe_ansehen", gruppe_id=gruppe_id, discord=meldung) + "#discord")


@app.route("/gruppe/<gruppe_id>/discord", methods=["POST"])
def gruppe_discord_verbinden(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    name, icon, _ = _gruppe_oder_404(cur, conn, gruppe_id)
    webhook = request.form.get("webhook", "").strip()

    if not ist_gueltige_webhook_url(webhook):
        meldung = "ungueltig"
    else:
        # Erst speichern, wenn Discord die Begrüßung tatsächlich angenommen hat
        gruppen_url = PUBLIC_BASE_URL + url_for("gruppe_ansehen", gruppe_id=gruppe_id)
        status = sende(webhook, [verbunden_embed(name, icon, gruppen_url)], PUBLIC_BASE_URL)
        if 200 <= status < 300:
            cur.execute(
                "UPDATE gruppen SET discord_webhook = %s, discord_seit = now() WHERE id = %s;",
                (webhook, gruppe_id),
            )
            conn.commit()
            meldung = "verbunden"
        else:
            meldung = "nicht_erreichbar"

    cur.close()
    conn.close()
    return _zur_gruppe(gruppe_id, meldung)


@app.route("/gruppe/<gruppe_id>/discord/test", methods=["POST"])
def gruppe_discord_test(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    name, icon, webhook = _gruppe_oder_404(cur, conn, gruppe_id)
    cur.close()
    conn.close()
    if not webhook:
        return _zur_gruppe(gruppe_id, "test_fehler")
    gruppen_url = PUBLIC_BASE_URL + url_for("gruppe_ansehen", gruppe_id=gruppe_id)
    status = sende(webhook, [test_embed(name, icon, gruppen_url)], PUBLIC_BASE_URL)
    return _zur_gruppe(gruppe_id, "test_ok" if 200 <= status < 300 else "test_fehler")


@app.route("/gruppe/<gruppe_id>/discord/trennen", methods=["POST"])
def gruppe_discord_trennen(gruppe_id):
    conn = get_connection()
    cur = conn.cursor()
    _gruppe_oder_404(cur, conn, gruppe_id)
    cur.execute("UPDATE gruppen SET discord_webhook = NULL, discord_seit = NULL WHERE id = %s;", (gruppe_id,))
    conn.commit()
    cur.close()
    conn.close()
    return _zur_gruppe(gruppe_id, "getrennt")
