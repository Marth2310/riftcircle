"""Direkter 1-vs-1-Vergleich zweier Matches (z.B. zwei Mitglieder einer Gruppe, oder zwei
eigene Spiele gegenübergestellt) - baut pro Statistik eine Balken-Zeile plus wer besser war,
nach demselben Muster wie der Lane-Vergleich auf der Match-Detail-Seite."""


def _fmt_int(v):
    return f"{v:,}".replace(",", ".")


def _fmt_kda(v):
    return f"{v:.2f}"


# (Key, Label, niedriger-ist-besser, Formatierer)
VERGLEICH_STATS = [
    ("kills", "Kills", False, _fmt_int),
    ("deaths", "Tode", True, _fmt_int),
    ("assists", "Assists", False, _fmt_int),
    ("kda", "KDA", False, _fmt_kda),
    ("cs", "CS", False, _fmt_int),
    ("vision_score", "Vision Score", False, _fmt_int),
    ("damage_dealt", "Schaden", False, _fmt_int),
    ("gold_earned", "Gold", False, _fmt_int),
]


def vergleiche(a, b):
    """a, b: dicts mit 'name' + den obigen Stat-Keys. Gibt eine Liste von Zeilen zurück,
    jede mit Balken-Anteil (a_pct) und wer in dieser Kategorie besser war (a_besser: True/
    False/None bei Gleichstand)."""
    zeilen = []
    for key, label, niedriger_besser, fmt in VERGLEICH_STATS:
        wert_a, wert_b = a[key], b[key]
        gesamt = wert_a + wert_b
        a_pct = round(wert_a / gesamt * 100) if gesamt else 50

        a_besser = None
        if wert_a != wert_b:
            a_besser = (wert_a < wert_b) if niedriger_besser else (wert_a > wert_b)

        zeilen.append({
            "label": label,
            "wert_a_text": fmt(wert_a), "wert_b_text": fmt(wert_b),
            "a_pct": a_pct, "a_besser": a_besser,
        })
    return zeilen


def fazit_saetze(a, b, zeilen):
    """Ein Satz pro Kategorie, in der jemand klar besser war - für die Zusammenfassung
    unter dem Balken-Vergleich."""
    saetze = []
    for z in zeilen:
        if z["a_besser"] is None:
            continue
        gewinner, verlierer = (a, b) if z["a_besser"] else (b, a)
        wert_gewinner = z["wert_a_text"] if z["a_besser"] else z["wert_b_text"]
        wert_verlierer = z["wert_b_text"] if z["a_besser"] else z["wert_a_text"]
        saetze.append(
            f"{gewinner['name']} hatte bei {z['label']} die Nase vorn "
            f"({wert_gewinner} vs. {wert_verlierer} von {verlierer['name']})."
        )
    return saetze
