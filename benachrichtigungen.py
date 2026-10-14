"""Benachrichtigungen: wer folgt mir, hat mein Spiel kommentiert, für mein Achievement gestimmt
oder mich in eine Gruppe geholt. Auslöser rufen benachrichtige()/benachrichtige_riot_inhaber()
auf - ein Fehler hier darf nie die eigentliche Aktion (Folgen, Kommentieren, ...) verhindern."""
from flask import g

from app_core import app
from db import get_connection
from web_hilfen import angemeldete_discord_id, relative_zeit

TYPEN = {
    "folgen": ("Neue Follower", "Jemand folgt deinem Profil"),
    "kommentar": ("Kommentare", "Jemand kommentiert eines deiner Spiele"),
    "stimme": ("Stimmen", "Jemand stimmt für ein Achievement von dir"),
    "gruppe": ("Gruppen", "Du wirst zu einer Gruppe hinzugefügt"),
}
MAX_ANZEIGE = 40
AUFBEWAHRUNG_TAGE = 90


def benachrichtige(cur, discord_id, typ, text, link=None, schluessel=None, von_discord_id=None):
    """Legt eine Benachrichtigung an. Keine für sich selbst, keine für abgeschaltete Typen,
    kein Doppel bei gleichem schluessel. True, wenn eine entstanden ist."""
    if not discord_id or discord_id == von_discord_id:
        return False
    cur.execute("SELECT COALESCE(benachrichtigung_aus, '{}') FROM nutzer WHERE discord_id = %s;", (discord_id,))
    row = cur.fetchone()
    if row is None or typ in row[0]:
        return False
    cur.execute("""
        INSERT INTO benachrichtigungen (discord_id, typ, text, link, schluessel)
        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (discord_id, schluessel) DO NOTHING;
    """, (discord_id, typ, text[:300], link, schluessel))
    return cur.rowcount == 1


def benachrichtige_riot_inhaber(cur, puuid, typ, text, link=None, schluessel=None, von_discord_id=None):
    """Wie benachrichtige(), adressiert aber an das Konto, das diesen Riot-Account verknüpft hat
    (kein Konto verknüpft -> niemand zu benachrichtigen)."""
    cur.execute("SELECT discord_id FROM nutzer WHERE riot_puuid = %s;", (puuid,))
    row = cur.fetchone()
    return benachrichtige(cur, row[0], typ, text, link, schluessel, von_discord_id) if row else False


def sicher(cur, aktion):
    """Führt einen Auslöser aus und schluckt Fehler - Benachrichtigungen sind Beiwerk. Ein
    Savepoint hält die Transaktion der eigentlichen Aktion (Folgen, Kommentar, ...) intakt,
    auch wenn der Benachrichtigungs-SQL scheitert."""
    cur.execute("SAVEPOINT benachrichtigung;")
    try:
        ergebnis = aktion()
        cur.execute("RELEASE SAVEPOINT benachrichtigung;")
        return ergebnis
    except Exception as e:
        print(f"Benachrichtigung fehlgeschlagen: {e!r}")
        cur.execute("ROLLBACK TO SAVEPOINT benachrichtigung;")
        return False


def ungelesen_anzahl():
    """Für die Glocke in der Navigation; pro Request einmal gelesen. 0 bei jedem Fehler."""
    discord_id = angemeldete_discord_id()
    if not discord_id:
        return 0
    if "ungelesen" not in g:
        g.ungelesen = 0
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM benachrichtigungen WHERE discord_id = %s AND NOT gelesen;", (discord_id,)
            )
            g.ungelesen = cur.fetchone()[0]
            cur.close()
            conn.close()
        except Exception:
            pass
    return g.ungelesen


def liste_fuer(cur, discord_id):
    cur.execute(
        "DELETE FROM benachrichtigungen WHERE discord_id = %s AND erstellt_am < now() - %s * interval '1 day';",
        (discord_id, AUFBEWAHRUNG_TAGE),
    )
    cur.execute("""
        SELECT id, typ, text, link, gelesen, erstellt_am FROM benachrichtigungen
        WHERE discord_id = %s ORDER BY erstellt_am DESC, id DESC LIMIT %s;
    """, (discord_id, MAX_ANZEIGE))
    return [
        {"id": i, "typ": t, "text": x, "link": l, "gelesen": gel, "zeit": relative_zeit(z)}
        for i, t, x, l, gel, z in cur.fetchall()
    ]


def alle_gelesen(cur, discord_id):
    cur.execute("UPDATE benachrichtigungen SET gelesen = TRUE WHERE discord_id = %s AND NOT gelesen;", (discord_id,))


app.add_template_global(ungelesen_anzahl, name="ungelesen_anzahl")
