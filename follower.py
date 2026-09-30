"""Folgen: einseitiges Beobachten eines Profils, unabhängig von Gruppen - jedes eingeloggte
Discord-Konto kann jedem getrackten Profil folgen, öffentlich sichtbar auf der Profilseite
(Follower-/Folgt-Zahl + Liste), ohne dass die andere Seite zustimmen oder zurückfolgen muss."""

MAX_LISTE = 24


def ist_gefolgt(cur, discord_id, ziel_puuid):
    if not discord_id:
        return False
    cur.execute("SELECT 1 FROM follower WHERE discord_id = %s AND ziel_puuid = %s;", (discord_id, ziel_puuid))
    return cur.fetchone() is not None


def follower_info(cur, puuid):
    """Follower (wer diesem Profil folgt) + Folgt (wem dieses Profil folgt - nur falls der
    Profil-Besitzer selbst ein verknüpftes Discord-Konto hat) - je mit echter Gesamtzahl und
    einer auf MAX_LISTE begrenzten, klickbaren Liste. In der Liste tauchen nur Follower auf,
    die selbst einen verknüpften Riot-Account haben (sonst gäbe es kein Profil zum Verlinken) -
    die Gesamtzahl zählt trotzdem alle."""
    cur.execute("SELECT COUNT(*) FROM follower WHERE ziel_puuid = %s;", (puuid,))
    follower_anzahl = cur.fetchone()[0]
    cur.execute("""
        SELECT pl.puuid, pl.riot_name, pl.riot_tag
        FROM follower f
        JOIN nutzer n ON n.discord_id = f.discord_id
        JOIN players pl ON pl.puuid = n.riot_puuid
        WHERE f.ziel_puuid = %s
        ORDER BY f.erstellt_am DESC
        LIMIT %s;
    """, (puuid, MAX_LISTE))
    follower = [{"puuid": p, "name": n, "tag": t} for p, n, t in cur.fetchall()]

    cur.execute("""
        SELECT COUNT(*) FROM follower f JOIN nutzer n ON n.discord_id = f.discord_id
        WHERE n.riot_puuid = %s;
    """, (puuid,))
    folgt_anzahl = cur.fetchone()[0]
    cur.execute("""
        SELECT pl.puuid, pl.riot_name, pl.riot_tag
        FROM follower f
        JOIN nutzer eigenes ON eigenes.riot_puuid = %s
        JOIN players pl ON pl.puuid = f.ziel_puuid
        WHERE f.discord_id = eigenes.discord_id
        ORDER BY f.erstellt_am DESC
        LIMIT %s;
    """, (puuid, MAX_LISTE))
    folgt = [{"puuid": p, "name": n, "tag": t} for p, n, t in cur.fetchall()]

    return {
        "follower_anzahl": follower_anzahl, "follower": follower,
        "folgt_anzahl": folgt_anzahl, "folgt": folgt,
    }
