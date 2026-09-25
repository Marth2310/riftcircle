"""Startseite, Such-API, Statistik-Dashboard und kleine Hilfsrouten (Favicon, riot.txt)."""
import secrets

from flask import abort, jsonify, render_template, request

from app_core import ANALYTICS_SECRET, app
from db import get_connection
from riot_assets import get_ddragon_version, summoner_icon_url
from web_hilfen import (
    GRUPPEN_ICONS,
    lese_meine_gruppen,
    lese_zuletzt_gesehen,
    mit_farbe,
    mit_summoner_icons,
)


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


@app.route("/riot.txt")
def riot_verification():
    """Domain-Verifizierung für die Riot-Production-API-Key-Bewerbung - der Verifizierungscode
    muss unter https://riftcircle.com/riot.txt erreichbar sein, nichts weiter im Response."""
    return "08e82a1c-c99b-47d6-b243-f819bd38890e", 200, {"Content-Type": "text/plain"}


@app.route("/")
def landing():
    zuletzt_gesehen = mit_summoner_icons(mit_farbe(lese_zuletzt_gesehen()))
    meine_gruppen = lese_meine_gruppen()
    return render_template(
        "landing.html", zuletzt_gesehen=zuletzt_gesehen, meine_gruppen=meine_gruppen,
        gruppen_icons=GRUPPEN_ICONS,
    )


MAX_SUCH_VORSCHLAEGE = 8


@app.route("/api/spieler-suche")
def spieler_suche():
    """Vorschläge beim Tippen (wie bei OP.GG): Präfix-Suche im eigenen Namens-Index. Riot selbst
    kennt keine Suche ohne Tag - gefunden wird also nur, wer schon in einem geladenen Match
    auftauchte. "Name#Ta" filtert zusätzlich nach dem Tag-Anfang."""
    eingabe = request.args.get("q", "").strip()
    name, _, tag_anfang = eingabe.partition("#")
    name, tag_anfang = name.strip(), tag_anfang.strip().lower()
    if len(name) < 2:
        return jsonify([])

    # LIKE-Sonderzeichen im Namen maskieren (Escape-Zeichen "!"), damit z.B. ein "_" im
    # Namen nicht als Platzhalter für ein beliebiges Zeichen gilt
    muster = name.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.riot_name, b.riot_tag, pl.profile_icon_id
        FROM bekannte_spieler b
        LEFT JOIN players pl ON pl.puuid = b.puuid AND NOT COALESCE(pl.is_meta_sample, FALSE)
        WHERE lower(b.riot_name) LIKE lower(%s) ESCAPE '!'
          AND lower(b.riot_tag) LIKE %s ESCAPE '!'
        ORDER BY (lower(b.riot_name) = lower(%s)) DESC, (pl.puuid IS NOT NULL) DESC,
                 b.zuletzt_gesehen DESC
        LIMIT %s;
    """, (muster, tag_anfang.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%",
          name, MAX_SUCH_VORSCHLAEGE))
    zeilen = cur.fetchall()
    cur.close()
    conn.close()

    version = get_ddragon_version()
    return jsonify([
        {"name": n, "tag": t, "icon": summoner_icon_url(icon_id, version) if icon_id else None}
        for n, t, icon_id in zeilen
    ])


@app.route("/stats/<key>")
def seite_statistik(key):
    """Eigenes Statistik-Dashboard - kein Login, wie bei den Gruppen schützt nur ein
    unerratbares Secret in der URL den Zugriff (falsches/fehlendes Secret -> 404, verrät also
    nicht mal, dass die Route existiert)."""
    if not ANALYTICS_SECRET or not secrets.compare_digest(key, ANALYTICS_SECRET):
        abort(404)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT visitor_hash) FROM page_views;")
    gesamt_views, gesamt_besucher = cur.fetchone()

    cur.execute("""
        SELECT COUNT(*), COUNT(DISTINCT visitor_hash) FROM page_views
        WHERE created_at >= now() - interval '7 days';
    """)
    views_7t, besucher_7t = cur.fetchone()

    cur.execute("""
        SELECT COUNT(*), COUNT(DISTINCT visitor_hash) FROM page_views
        WHERE created_at >= now() - interval '30 days';
    """)
    views_30t, besucher_30t = cur.fetchone()

    cur.execute("""
        SELECT path, COUNT(*) AS n
        FROM page_views
        WHERE created_at >= now() - interval '30 days'
        GROUP BY path
        ORDER BY n DESC
        LIMIT 12;
    """)
    top_seiten = [{"path": p, "views": n} for p, n in cur.fetchall()]

    # Alle letzten 14 Tage inkl. Tage ganz ohne Aufrufe (sonst hat der Balken-Chart Lücken)
    cur.execute("""
        SELECT d::date,
               COUNT(pv.id) AS views,
               COUNT(DISTINCT pv.visitor_hash) AS besucher
        FROM generate_series(
            (now() - interval '13 days')::date, now()::date, interval '1 day'
        ) d
        LEFT JOIN page_views pv ON date_trunc('day', pv.created_at) = d
        GROUP BY d
        ORDER BY d;
    """)
    tagesverlauf = [{"tag": t.strftime("%d.%m."), "views": v, "besucher": b} for t, v, b in cur.fetchall()]
    max_views = max([t["views"] for t in tagesverlauf] + [1])

    cur.close()
    conn.close()

    return render_template(
        "stats.html",
        gesamt_views=gesamt_views, gesamt_besucher=gesamt_besucher,
        views_7t=views_7t, besucher_7t=besucher_7t,
        views_30t=views_30t, besucher_30t=besucher_30t,
        top_seiten=top_seiten, tagesverlauf=tagesverlauf, max_views=max_views,
    )
