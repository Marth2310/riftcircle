"""Profilseite eines Spielers inkl. Suche ohne Tag."""
import random

from flask import abort, make_response, redirect, render_template, request, url_for

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
from app_core import app, headers
from benchmarks import normalize_tier
from champion_stats import get_champion_by_key, get_champion_by_numeric_id
from db import get_connection
from follower import follower_info, ist_gefolgt
from riot_assets import (
    champion_icon_url,
    champion_skin_splashes,
    champion_splash_url,
    get_ddragon_version,
    get_summoner_icon_id,
    item_icon_url,
    summoner_icon_url,
)
from riot_fetch import (
    RiotApiNichtVerfuegbar,
    SummonerNotFound,
    get_top_mastery_champion_ids,
    sync_player,
)
from profil_statistik import duo_partner, rang_speichern, rang_verlauf, rollen_statistik, tier_achse
from riot_runes import keystone_and_secondary_icons
from trophaeen import trophaeen_fuer_spieler
from seiten_champions import ROLLEN_ICON_URL
from seiten_gruppen import benachrichtige_gruppe_sicher
from spielmodus import KATEGORIEN as MODUS_KATEGORIEN
from web_hilfen import (
    angemeldeter_nutzer,
    cookie_setzen,
    lese_zuletzt_gesehen,
    merke_zuletzt_gesehen,
    mit_farbe,
    mit_summoner_icons,
    neue_zuletzt_gesehen_liste,
    spieler_mit_namen,
    relative_zeit,
)


MAX_MEISTGESPIELTE = 12

RANG_TIERS = {
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND",
    "MASTER", "GRANDMASTER", "CHALLENGER",
}
# Ab Master gibt es keine Divisionen mehr (Riot liefert trotzdem "I")
OHNE_DIVISION = {"MASTER", "GRANDMASTER", "CHALLENGER"}


def rang_info(eintrag):
    """Anzeige-Daten eines Rangs für den Profilkopf (Emblem, Text, LP, Saison-Bilanz) - None
    heißt Unranked. Embleme liegen zugeschnitten unter static/rang-embleme/ (Quelle: Community
    Dragon, dort 2560px groß mit viel Leerraum)."""
    if not eintrag or eintrag.get("tier") not in RANG_TIERS:
        return None
    tier = eintrag["tier"]
    spiele = eintrag["wins"] + eintrag["losses"]
    return {
        "tier": tier.lower(),
        "text": tier.capitalize() if tier in OHNE_DIVISION else f"{tier.capitalize()} {eintrag['rank']}",
        "lp": eintrag["lp"],
        "siege": eintrag["wins"],
        "niederlagen": eintrag["losses"],
        "winrate": round(eintrag["wins"] / spiele * 100) if spiele else 0,
        "emblem": url_for("static", filename=f"rang-embleme/{tier.lower()}.png"),
    }


def schnittwerte(spiele):
    """Durchschnitt über die analysierten Spiele für die Kennzahlen im Profilkopf."""
    if not spiele:
        return None
    n = len(spiele)
    kills = sum(s["kills"] for s in spiele)
    deaths = sum(s["deaths"] for s in spiele)
    assists = sum(s["assists"] for s in spiele)
    kp = [s["kill_participation"] for s in spiele if s["kill_participation"] is not None]
    return {
        "kda": round((kills + assists) / deaths, 2) if deaths else float(kills + assists),
        "kills": round(kills / n, 1), "deaths": round(deaths / n, 1), "assists": round(assists / n, 1),
        "cs_min": round(sum(s["cs_per_min"] for s in spiele) / n, 1),
        "vision_min": round(sum(s["vision_per_min"] for s in spiele) / n, 2),
        "kp": round(sum(kp) / len(kp) * 100) if kp else None,
    }


@app.route("/profil")
def profil():
    riot_id_input = request.args.get("riot_id", "").strip()
    fehler = None
    puuid = None
    namens_treffer = []

    conn = get_connection()
    cur = conn.cursor()

    if riot_id_input:
        geraten = False
        if "#" in riot_id_input:
            gesuchter_name, gesuchter_tag = riot_id_input.rsplit("#", 1)
            gesuchter_name, gesuchter_tag = gesuchter_name.strip(), gesuchter_tag.strip()
        else:
            # Riot bietet keine "alle Accounts mit diesem Namen"-Suche an - nur exakte
            # Name#Tag-Lookups. Deshalb erst im eigenen Namens-Index nachsehen (jeder Spieler
            # aus einem geladenen Match): eindeutig -> direkt dieses Profil, mehrere -> Auswahl.
            # Unbekannt -> Best-Effort "#EUW" (viele frühe EUW-Accounts bekamen beim Umstieg
            # auf Riot IDs automatisch diesen Tag).
            gesuchter_name = riot_id_input.strip()
            namens_treffer = spieler_mit_namen(cur, gesuchter_name)
            if len(namens_treffer) == 1:
                gesuchter_name, gesuchter_tag = namens_treffer[0]["name"], namens_treffer[0]["tag"]
                namens_treffer = []
            else:
                gesuchter_tag = "EUW"
                geraten = True

    if riot_id_input and not namens_treffer:
        try:
            # Holt bei Bedarf neue Spiele nach - bereits gespeicherte Matches werden
            # übersprungen, wiederholte Suchen nach demselben Profil sind daher schnell.
            puuid, neue_spiele = sync_player(cur, conn, headers, gesuchter_name, gesuchter_tag)
            if neue_spiele:
                cur.execute("""
                    SELECT gm.gruppe_id FROM gruppen_mitglieder gm JOIN gruppen g ON g.id = gm.gruppe_id
                    WHERE gm.puuid = %s AND g.discord_webhook IS NOT NULL;
                """, (puuid,))
                for (gruppe_id,) in cur.fetchall():
                    benachrichtige_gruppe_sicher(cur, conn, gruppe_id)
        except RiotApiNichtVerfuegbar as e:
            fehler = str(e)
        except SummonerNotFound as e:
            if geraten:
                fehler = (
                    f'Riot erlaubt leider keine Suche über alle Tags hinweg - wir haben automatisch '
                    f'"{gesuchter_name}#EUW" probiert: {e}'
                    f' Bitte gib den genauen Tag an, z.B. {gesuchter_name}#1234.'
                )
            else:
                fehler = str(e)
    elif not riot_id_input:
        # Kein Suchbegriff: auf das zuletzt von DIESEM Browser angesehene Profil zurückfallen
        # (statt eines global letzten Profils aus der DB - jeder Besucher soll nur sein
        # eigenes zuletzt gesehenes Profil sehen, nicht das irgendeines anderen Nutzers).
        zg = lese_zuletzt_gesehen()
        if zg:
            puuid = zg[0]["puuid"]

    if puuid is None:
        cur.close()
        conn.close()
        return render_template(
            "dashboard.html",
            kein_spieler=True,
            fehler=fehler,
            riot_id_input=riot_id_input,
            namens_treffer=mit_summoner_icons(mit_farbe(namens_treffer)),
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

    # Meistgespielte Champions über ALLE gespeicherten Spiele des Spielers (nicht nur die
    # letzten ANZAHL_MATCHES der Analyse oben)
    cur.execute("""
        SELECT champion, COUNT(*) AS spiele, SUM(CASE WHEN win THEN 1 ELSE 0 END) AS siege,
               SUM(kills), SUM(deaths), SUM(assists)
        FROM participants
        WHERE puuid = %s
        GROUP BY champion
        ORDER BY spiele DESC, siege DESC, champion
        LIMIT %s;
    """, (puuid, MAX_MEISTGESPIELTE))
    champ_zeilen = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM participants WHERE puuid = %s;", (puuid,))
    gespeicherte_spiele = cur.fetchone()[0]
    cur.execute("SELECT discord_name, discord_avatar FROM nutzer WHERE riot_puuid = %s;", (puuid,))
    discord_row = cur.fetchone()
    profil_discord = {"name": discord_row[0], "avatar": discord_row[1]} if discord_row else None
    ich = angemeldeter_nutzer()
    ist_mein_profil = bool(ich and ich.get("puuid") == puuid)

    rang_speichern(cur, puuid, ranks)
    conn.commit()
    verlauf_rang = rang_verlauf(cur, puuid)
    rollen_stats = rollen_statistik(cur, puuid)
    partner = duo_partner(cur, puuid)
    trophaeen = trophaeen_fuer_spieler(cur, puuid)
    follower = follower_info(cur, puuid)
    folge_ich = ist_gefolgt(cur, ich["id"] if ich else None, puuid)
    # Nur Gruppen, die sich selbst öffentlich gemacht haben - private Gruppen sind nur per
    # Link erreichbar und dürfen über ein fremdes Profil nicht auffindbar werden.
    cur.execute("""
        SELECT g.id, g.name, COALESCE(g.icon, '🛡️') FROM gruppen g
        JOIN gruppen_mitglieder gm ON gm.gruppe_id = g.id
        WHERE gm.puuid = %s AND g.oeffentlich
        ORDER BY g.name;
    """, (puuid,))
    oeffentliche_gruppen = [{"id": i, "name": n, "icon": ic} for i, n, ic in cur.fetchall()]

    profile_icon_id = get_summoner_icon_id(puuid, headers)
    if profile_icon_id is not None:
        cur.execute("UPDATE players SET profile_icon_id = %s WHERE puuid = %s;", (profile_icon_id, puuid))
        conn.commit()
    cur.close()
    conn.close()

    ddragon_version = get_ddragon_version()

    # Seitenhintergrund: einer der 3 Mastery-Champions ("Mains"), zufällig pro Aufruf, dessen
    # Skins im Browser nacheinander überblendet werden. Fällt auf den zuletzt gespielten Champion
    # zurück, falls die Mastery-API fehlschlägt oder (bei brandneuen Accounts) noch keine
    # Mastery-Punkte existieren.
    mains = [
        champ for champ in (get_champion_by_numeric_id(cid) for cid in get_top_mastery_champion_ids(puuid, headers))
        if champ
    ]
    hero_champion = random.choice(mains)["key"] if mains else (rows[0][1] if rows else None)
    hintergrund_skins = []
    if hero_champion:
        try:
            hintergrund_skins = champion_skin_splashes(hero_champion)
        except Exception:
            hintergrund_skins = [champion_splash_url(hero_champion)]

    meistgespielte = []
    for champion, spiele_anzahl, siege_anzahl, kills, deaths, assists in champ_zeilen:
        champ_info = get_champion_by_key(champion)
        meistgespielte.append({
            "champion": champion,
            "name": champ_info["name"] if champ_info else champion,  # "Twisted Fate" statt "TwistedFate"
            "icon": champion_icon_url(champion, ddragon_version),
            "splash": champion_splash_url(champion),
            "spiele": spiele_anzahl,
            "siege": siege_anzahl,
            "niederlagen": spiele_anzahl - siege_anzahl,
            "winrate": round(siege_anzahl / spiele_anzahl * 100),
            "kda": round((kills + assists) / deaths, 2) if deaths else float(kills + assists),
        })

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
    neue_liste = neue_zuletzt_gesehen_liste(puuid, riot_name, riot_tag)
    merke_zuletzt_gesehen(puuid)

    resp = make_response(render_template(
        "dashboard.html",
        kein_spieler=False,
        modus_kategorien=MODUS_KATEGORIEN,
        fehler=fehler,
        riot_id_input=riot_id_input,
        puuid=puuid,
        riot_name=riot_name,
        riot_tag=riot_tag,
        rang_solo=anzeige_rang_solo,
        rang_flex=anzeige_rang_flex,
        raenge=[("Solo/Duo", rang_info(solo_rang)), ("Flex", rang_info(flex_rang))],
        schnitt=schnittwerte(spiele),
        oeffentliche_gruppen=oeffentliche_gruppen,
        summoner_icon=summoner_icon_url(profile_icon_id, ddragon_version) if profile_icon_id else None,
        hintergrund_skins=hintergrund_skins,
        meistgespielte=meistgespielte,
        rang_verlauf=verlauf_rang,
        tier_achse=tier_achse(),
        rollen_stats=rollen_stats,
        rollen_icon_url=ROLLEN_ICON_URL,
        duo_partner=partner,
        trophaeen=trophaeen,
        follower=follower,
        folge_ich=folge_ich,
        ich=ich,
        gespeicherte_spiele=gespeicherte_spiele,
        profil_discord=profil_discord,
        ist_mein_profil=ist_mein_profil,
        anzahl_spiele=len(spiele),
        siege=siege,
        niederlagen=len(spiele) - siege,
        spiele=spiele,
        probleme=probleme,
        staerken=staerken,
        kda_chart=kda_chart,
    ))
    return cookie_setzen(resp, neue_liste)


def _zurueck_zum_profil(riot_name, riot_tag):
    if riot_name and riot_tag:
        return redirect(url_for("profil", riot_id=f"{riot_name}#{riot_tag}"))
    return redirect(url_for("landing"))


@app.route("/folgen", methods=["POST"])
def profil_folgen():
    """Folgen ist unabhängig von Gruppen - jedes eingeloggte Discord-Konto kann jedem
    getrackten Profil folgen, öffentlich sichtbar (siehe follower.py)."""
    ich = angemeldeter_nutzer()
    ziel_puuid = request.form.get("ziel_puuid", "").strip()
    riot_name = request.form.get("riot_name", "").strip()
    riot_tag = request.form.get("riot_tag", "").strip()

    if not ich:
        return redirect(url_for("konto"))
    if not ziel_puuid or ziel_puuid == ich.get("puuid"):
        return _zurueck_zum_profil(riot_name, riot_tag)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM players WHERE puuid = %s;", (ziel_puuid,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        abort(404)
    cur.execute(
        "INSERT INTO follower (discord_id, ziel_puuid) VALUES (%s, %s) ON CONFLICT DO NOTHING;",
        (ich["id"], ziel_puuid),
    )
    conn.commit()
    cur.close()
    conn.close()
    return _zurueck_zum_profil(riot_name, riot_tag)


@app.route("/entfolgen", methods=["POST"])
def profil_entfolgen():
    ich = angemeldeter_nutzer()
    ziel_puuid = request.form.get("ziel_puuid", "").strip()
    riot_name = request.form.get("riot_name", "").strip()
    riot_tag = request.form.get("riot_tag", "").strip()

    if not ich:
        return redirect(url_for("konto"))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM follower WHERE discord_id = %s AND ziel_puuid = %s;", (ich["id"], ziel_puuid))
    conn.commit()
    cur.close()
    conn.close()
    return _zurueck_zum_profil(riot_name, riot_tag)
