"""Discord-Webhooks für Gruppen: Validierung, Senden und Aufbau der Nachrichten (Embeds).
Welche Nachrichten wann rausgehen, entscheidet dashboard.py (benachrichtige_gruppe)."""
import re

import requests

from riot_assets import REQUEST_TIMEOUT

# Nur echte Discord-Webhook-URLs - der Server schickt an diese Adresse POST-Requests, eine
# beliebige URL wäre also ein Einfallstor, um RiftCircle Anfragen an fremde Server schicken
# zu lassen.
WEBHOOK_MUSTER = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,25}/[A-Za-z0-9_-]{20,100}$"
)
MAX_EMBEDS_PRO_NACHRICHT = 10  # Limit von Discord

FARBE_ACHIEVEMENT = 0xD4AF6A  # --gold
FARBE_WOCHE = 0x3DD6C6  # --teal (Gruppen-Farbe)
FARBE_INFO = 0x8B6CF5  # --accent

MEDAILLEN = ["🥇", "🥈", "🥉", "4.", "5."]


def ist_gueltige_webhook_url(url):
    return bool(WEBHOOK_MUSTER.match(url or ""))


def erwaehnung(discord_ids, anlass):
    """Nachrichtentext, der die verknüpften Spieler anpingt - Erwähnungen in Embeds lösen
    in Discord keine Benachrichtigung aus, nur im normalen Nachrichtentext."""
    if not discord_ids:
        return None
    return f"{anlass} " + " ".join(f"<@{i}>" for i in discord_ids)


def sende(webhook_url, embeds, basis_url, inhalt=None, erwaehnte_ids=()):
    """Schickt eine Nachricht mit bis zu 10 Embeds. Gibt den HTTP-Status zurück (0 bei
    Netzwerkfehler) - 204 heißt angekommen, 401/404 heißt der Webhook wurde in Discord
    gelöscht. Gepingt werden ausschließlich erwaehnte_ids (kein @everyone, keine Rollen)."""
    nachricht = {
        "username": "RiftCircle",
        "avatar_url": f"{basis_url}/static/apple-touch-icon.png",
        "embeds": embeds[:MAX_EMBEDS_PRO_NACHRICHT],
        "allowed_mentions": {"parse": [], "users": list(erwaehnte_ids)[:100]},
    }
    if inhalt:
        nachricht["content"] = inhalt
    try:
        resp = requests.post(webhook_url, json=nachricht, timeout=REQUEST_TIMEOUT)
        return resp.status_code
    except requests.RequestException:
        return 0


def verbunden_embed(gruppe_name, gruppe_icon, gruppen_url):
    return {
        "title": f"{gruppe_icon} {gruppe_name} ist mit RiftCircle verbunden",
        "description": (
            "Ab jetzt landen hier **Achievements** (Pentakill, perfektes Spiel, gestohlene "
            "Objectives, ...) aus neuen Spielen der Gruppe und nach jeder Woche der **Wochensieger**."
        ),
        "url": gruppen_url,
        "color": FARBE_INFO,
    }


def test_embed(gruppe_name, gruppe_icon, gruppen_url):
    return {
        "title": "Testnachricht",
        "description": f"Die Verbindung von **{gruppe_icon} {gruppe_name}** zu diesem Kanal funktioniert.",
        "url": gruppen_url,
        "color": FARBE_INFO,
    }


def achievement_embed(spiel, gruppe_name, gruppe_icon, match_url):
    """spiel = ein Eintrag aus baue_gruppen_feed() mit gesetztem "achievement"."""
    ergebnis = "Sieg" if spiel["win"] else "Niederlage"
    return {
        "title": f"{spiel['achievement']['icon']} {spiel['achievement']['text']}",
        "description": (
            f"**{spiel['riot_name']}** · {ergebnis} · "
            f"{spiel['kills']}/{spiel['deaths']}/{spiel['assists']} · {spiel['dauer_min']} Min."
        ),
        "url": match_url,
        "color": FARBE_ACHIEVEMENT,
        "thumbnail": {"url": spiel["champion_icon"]},
        "footer": {"text": f"{gruppe_icon} {gruppe_name}"},
    }


def wochen_embed(rangliste, kalenderwoche, gruppe_name, gruppe_icon, gruppen_url):
    """rangliste = baue_wochenrueckblick() für die abgeschlossene Kalenderwoche."""
    sieger = rangliste[0]
    multikills = [
        f"{anzahl}x {name}" for anzahl, name in (
            (sieger["pentas"], "Penta"), (sieger["quadras"], "Quadra"), (sieger["triples"], "Triple"),
        ) if anzahl
    ]
    beschreibung = (
        f"{sieger['spiele']} Spiel{'e' if sieger['spiele'] != 1 else ''} · {sieger['winrate']}% Winrate · "
        f"{sieger['avg_kda']} Ø-KDA" + (f" · {', '.join(multikills)}" if multikills else "")
    )
    zeilen = [
        f"{MEDAILLEN[i]} **{r['name']}** - Score {r['score']} ({r['spiele']} Sp., {r['winrate']}%)"
        for i, r in enumerate(rangliste[:len(MEDAILLEN)])
    ]
    return {
        "title": f"👑 Wochensieger {kalenderwoche}: {sieger['name']}",
        "description": beschreibung,
        "url": gruppen_url,
        "color": FARBE_WOCHE,
        "fields": [{"name": "Rangliste", "value": "\n".join(zeilen)}],
        "footer": {"text": f"{gruppe_icon} {gruppe_name} · Qualität zählt mehr als reine Spielanzahl"},
    }
