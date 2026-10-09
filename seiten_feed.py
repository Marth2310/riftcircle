"""Persönlicher Feed (/feed): die letzten Spiele aller Leute aus dem eigenen Kreis - wem man
folgt plus die Mitglieder der eigenen Gruppen - chronologisch gemischt, nach Tagen gruppiert.
Eigene Spiele bleiben draußen, die stehen im eigenen Profil."""
from datetime import date, timedelta

from flask import render_template

from app_core import app
from db import get_connection
from seiten_gruppen import baue_gruppen_feed
from web_hilfen import angemeldeter_nutzer, lese_meine_gruppen

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _tages_label(played_at):
    tag = played_at.date()
    heute = date.today()
    if tag == heute:
        return "Heute"
    if tag == heute - timedelta(days=1):
        return "Gestern"
    return f"{WOCHENTAGE[tag.weekday()]}, {tag.strftime('%d.%m.')}"


def kreis_quellen(cur, ich):
    """puuid -> woher jemand im Feed auftaucht ("Gefolgt" oder Name der Gruppe). Gefolgt
    gewinnt, wenn beides zutrifft - das ist die bewusstere Entscheidung."""
    quelle = {}
    gruppen = lese_meine_gruppen()
    if gruppen:
        namen = {g["id"]: g["name"] for g in gruppen}
        cur.execute(
            "SELECT gruppe_id, puuid FROM gruppen_mitglieder WHERE gruppe_id = ANY(%s);",
            ([g["id"] for g in gruppen],),
        )
        for gruppe_id, puuid in cur.fetchall():
            quelle.setdefault(puuid, namen.get(gruppe_id, "Gruppe"))
    cur.execute("SELECT ziel_puuid FROM follower WHERE discord_id = %s;", (ich["id"],))
    for (puuid,) in cur.fetchall():
        quelle[puuid] = "Gefolgt"
    quelle.pop(ich.get("puuid"), None)
    return quelle, len(gruppen)


@app.route("/feed")
def feed():
    ich = angemeldeter_nutzer()
    if not ich:
        return render_template("feed.html", ich=None)

    conn = get_connection()
    cur = conn.cursor()
    quelle, anzahl_gruppen = kreis_quellen(cur, ich)
    eintraege = baue_gruppen_feed(cur, list(quelle)) if quelle else []
    cur.close()
    conn.close()

    tage = []
    for e in eintraege:
        e["quelle"] = quelle.get(e["puuid"], "")
        label = _tages_label(e["played_at"]) if e["played_at"] else "Früher"
        if not tage or tage[-1]["label"] != label:
            tage.append({"label": label, "eintraege": []})
        tage[-1]["eintraege"].append(e)

    quellen_namen = sorted(set(quelle.values()), key=lambda q: (q != "Gefolgt", q.lower()))
    return render_template(
        "feed.html", ich=ich, tage=tage, anzahl_spiele=len(eintraege),
        anzahl_spieler=len(quelle), anzahl_gefolgt=sum(1 for q in quelle.values() if q == "Gefolgt"),
        anzahl_gruppen=anzahl_gruppen, quellen_namen=quellen_namen,
        anzahl_achievements=sum(1 for e in eintraege if e["achievement"]),
    )
