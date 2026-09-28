"""Riot-queueId -> Anzeigename + Filter-Kategorie (ranked / normal / aram / sonstige).

Quelle: https://static.developer.riotgames.com/docs/lol/queues.json - nur die gängigen
Queues sind benannt, alles andere läuft als "Sonstige" mit generischem Namen."""

KATEGORIEN = {
    "ranked": "Ranked",
    "normal": "Normal",
    "aram": "ARAM",
    "sonstige": "Sonstige",
}

QUEUES = {
    420: ("Ranked Solo/Duo", "ranked"),
    440: ("Ranked Flex", "ranked"),
    400: ("Normal Draft", "normal"),
    430: ("Normal Blind", "normal"),
    480: ("Swiftplay", "normal"),
    490: ("Quickplay", "normal"),
    450: ("ARAM", "aram"),
    720: ("ARAM Clash", "aram"),
    2400: ("ARAM Mayhem", "aram"),
    700: ("Clash", "sonstige"),
    900: ("ARURF", "sonstige"),
    1900: ("URF", "sonstige"),
    1700: ("Arena", "sonstige"),
    1710: ("Arena", "sonstige"),
    1750: ("Arena", "sonstige"),
    1300: ("Nexus Blitz", "sonstige"),
    0: ("Custom", "sonstige"),
}

# Co-op vs. KI hat viele IDs (Intro/Anfänger/Fortgeschritten, alt und neu)
KI_QUEUES = {830, 840, 850, 870, 880, 890}


def spielmodus(queue_id):
    """(Name, Kategorie) oder (None, None), solange die Queue unbekannt ist (alte Spiele
    von vor dem Speichern der queueId, die noch nicht nachgeladen wurden)."""
    if queue_id is None:
        return None, None
    if queue_id in QUEUES:
        return QUEUES[queue_id]
    if queue_id in KI_QUEUES:
        return "Co-op vs. KI", "sonstige"
    return "Sondermodus", "sonstige"
