"""Kommentare zu einem einzelnen Spiel/Achievement (match_id + puuid) - auf der Match-
Detailseite. Nicht an eine bestimmte Gruppe gebunden, weil die Match-Seite selbst keine kennt
(sie wird aus mehreren Gruppen-Feeds heraus verlinkt) - wer den Link hat, sieht denselben
Kommentar-Thread, unabhängig aus welcher Gruppe heraus er kam."""
from web_hilfen import relative_zeit

MAX_KOMMENTAR_LAENGE = 500


def kommentare_fuer_spiel(cur, match_id, puuid):
    cur.execute("""
        SELECT k.id, k.text, k.erstellt_am, k.discord_id, n.discord_name, n.discord_avatar
        FROM kommentare k JOIN nutzer n ON n.discord_id = k.discord_id
        WHERE k.match_id = %s AND k.puuid = %s
        ORDER BY k.erstellt_am;
    """, (match_id, puuid))
    return [
        {
            "id": kid, "text": text, "zeit_text": relative_zeit(erstellt_am),
            "discord_id": discord_id, "name": name, "avatar": avatar,
        }
        for kid, text, erstellt_am, discord_id, name, avatar in cur.fetchall()
    ]
