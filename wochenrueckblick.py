"""Wöchentliche Gruppen-Rangliste ("wer war diese Woche am besten") - gewichtet bewusst
Qualität (Durchschnittswerte pro Spiel) stärker als reines Spielvolumen, damit nicht
automatisch gewinnt, wer einfach am meisten Spiele gemacht hat. Wer 30 schlechte Spiele
macht, hat trotzdem einen niedrigen Ø-KDA/Winrate und damit einen niedrigen Score - Volumen
gibt nur einen kleinen, gedeckelten Bonus-Ausschlag bei ähnlicher Qualität. Multikills zählen
als absolute Bonuspunkte (ein zusätzlicher Pentakill ist ein echter Bonus, unabhängig davon,
in wie vielen Spielen er fiel). Die Gewichte sind eine erste plausible Einschätzung, keine
exakte Wissenschaft - gerne nach Gefühl nachjustieren."""

MULTIKILL_PUNKTE = {"penta": 40, "quadra": 15, "triple": 5, "double": 1}
VOLUMEN_DECKEL = 15  # ab so vielen Spielen gibt mehr Volumen keinen zusätzlichen Bonus mehr


def berechne_score(stats):
    """stats: dict mit spiele, siege, kills, deaths, assists, pentas, quadras, triples, doubles."""
    if stats["spiele"] == 0:
        return 0.0

    avg_kda = (
        (stats["kills"] + stats["assists"]) / stats["deaths"] if stats["deaths"] > 0
        else float(stats["kills"] + stats["assists"])
    )
    winrate = stats["siege"] / stats["spiele"]

    multikill_punkte = (
        stats["pentas"] * MULTIKILL_PUNKTE["penta"]
        + stats["quadras"] * MULTIKILL_PUNKTE["quadra"]
        + stats["triples"] * MULTIKILL_PUNKTE["triple"]
        + stats["doubles"] * MULTIKILL_PUNKTE["double"]
    )
    qualitaet = avg_kda * 8 + winrate * 25
    volumen_bonus = min(stats["spiele"], VOLUMEN_DECKEL) * 1.0

    return round(multikill_punkte + qualitaet + volumen_bonus, 1)
