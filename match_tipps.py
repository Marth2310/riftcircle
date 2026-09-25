"""Individuelle Tipps für EIN Spiel - statt fester Texte pro Rolle konkrete Beobachtungen mit
echten Zahlen und Zeitpunkten aus genau diesem Match: Richtwerte für Rang + Rolle, Tode,
Gold gegen den Lane-Gegner, Objectives, und Runen/Skills/erstes Item im Vergleich zur
Champion-Datenbank. Rein regelbasiert (keine KI). Jeder Punkt hat eine Priorität - angezeigt
werden nur die wichtigsten, dazu was gut lief."""
from statistics import median

from analysis import match_metrics
from champion_stats import _fertige_items, _get_item_info, berechne_runen_varianten, skill_prioritaet
from riot_runes import get_rune_trees
from riot_timeline import format_game_time

MAX_VERBESSERUNGEN = 5
MAX_STAERKEN = 3
GOLD_PRO_CS = 21  # grober Schnitt aus Nah-/Fernkampf-/Kanonen-Vasallen im Midgame
ROLLEN_TEXT = {"TOP": "Toplaner", "JUNGLE": "Jungler", "MIDDLE": "Midlaner", "BOTTOM": "ADCs", "UTILITY": "Supports"}
ROLLEN_EINZAHL = {"TOP": "Toplaner", "JUNGLE": "Jungler", "MIDDLE": "Midlaner", "BOTTOM": "ADC", "UTILITY": "Support"}
SKILL_BUCHSTABE = {1: "Q", 2: "W", 3: "E"}
OBJECTIVE_NAMEN = {
    "DRAGON": "Drachen", "ELDER_DRAGON": "Drachen", "RIFTHERALD": "Herald", "BARON_NASHOR": "Baron",
    "HORDE": "Void-Larven", "ATAKHAN": "Atakhan",
}
# Kill-Beteiligung hängt stark an der Rolle: Toplaner sind seltener an Kills beteiligt als
# Jungler oder Supports - Anpassung auf den (rollenunabhängigen) Richtwert aus benchmarks.py
KP_ROLLEN_ANPASSUNG = {"TOP": -0.12, "JUNGLE": 0.0, "MIDDLE": -0.05, "BOTTOM": -0.05, "UTILITY": 0.02}
MIN_SPIELE_VERGLEICH = 10  # Champion-Vergleiche erst ab so vielen Spielen in der Datenbank
MIN_SPIELE_ITEM_TIMING = 8

EINZEL_QUERY = """
    SELECT p.match_id, p.champion, p.role, p.kills, p.deaths, p.assists, p.cs, p.vision_score,
           m.duration_seconds, p.win, p.kill_participation, p.damage_share,
           p.turret_takedowns, p.objectives_stolen, p.solo_kills,
           p.items, p.champ_level, m.played_at, p.damage_rank, p.gold_earned, p.gold_diff,
           p.damage_dealt, p.perks
    FROM participants p JOIN matches m ON p.match_id = m.match_id
    WHERE p.puuid = %s AND p.match_id = %s;
"""


def _tipp(prioritaet, icon, titel, text):
    return {"prioritaet": prioritaet, "icon": icon, "titel": titel, "text": text}


def _zahl(wert, nachkomma=1):
    return f"{wert:.{nachkomma}f}".replace(".", ",")


def _gold(wert):
    return f"{abs(wert) / 1000:.1f}k".replace(".", ",")


# ---------------------------------------------------------------------------------------
# Einzelne Prüfungen - jede hängt Verbesserungen/Stärken an die übergebenen Listen an
# ---------------------------------------------------------------------------------------

def _pruefe_richtwerte(k, verbesserungen, staerken):
    vergleich, minuten, rolle, tier_name = k["vergleich"], k["minuten"], k["role"], k["tier_name"]
    wer = f"{tier_name}-{ROLLEN_TEXT[rolle]}" if rolle in ROLLEN_TEXT else f"Spieler auf {tier_name}"

    if "cs_per_min" in vergleich:
        wert, richtwert = vergleich["cs_per_min"]
        if wert < richtwert * 0.93:
            fehlende_cs = round((richtwert - wert) * minuten)
            handlung = (
                "Nimm zwischen Gänks und Objectives konsequent deine Camps mit - verfallene oder "
                "gestohlene Camps fehlen genau hier."
                if rolle == "JUNGLE" else
                "Lass zwischen Fights keine Wellen verfallen und sammle nach einem Recall zuerst die "
                "Welle ein, die gerade unter deinem Turm ankommt."
            )
            verbesserungen.append(_tipp(
                25 + (1 - wert / richtwert) * 100, "🌾", "Farm",
                f"{_zahl(wert)} CS pro Minute - {wer} liegen im Schnitt bei {_zahl(richtwert)}. "
                f"Über {round(minuten)} Minuten sind das rund {fehlende_cs} CS bzw. ~{_gold(fehlende_cs * GOLD_PRO_CS)} "
                f"Gold weniger. {handlung}",
            ))
        elif wert >= richtwert * 1.08:
            staerken.append(_tipp(
                (wert / richtwert - 1) * 100, "🌾", "Starker Farm",
                f"{_zahl(wert)} CS pro Minute - über dem Schnitt der {wer} ({_zahl(richtwert)}).",
            ))

    if "vision_per_min" in vergleich:
        wert, richtwert = vergleich["vision_per_min"]
        if wert < richtwert * 0.85:
            verbesserungen.append(_tipp(
                15 + (1 - wert / richtwert) * 60, "👁️", "Vision",
                f"Vision-Score {_zahl(wert, 2)} pro Minute - {wer} kommen auf etwa {_zahl(richtwert, 2)}. "
                f"Kauf bei jedem Recall einen Control Ward und platziere ihn vor dem nächsten Objective, "
                f"und nutz deinen Trinket, sobald er aufgeladen ist.",
            ))
        elif wert >= richtwert * 1.15:
            staerken.append(_tipp(
                (wert / richtwert - 1) * 80, "👁️", "Gute Vision",
                f"Vision-Score {_zahl(wert, 2)} pro Minute - deutlich über dem Schnitt der {wer} ({_zahl(richtwert, 2)}).",
            ))

    if "kill_participation" in vergleich:
        wert, richtwert = vergleich["kill_participation"]
        richtwert += KP_ROLLEN_ANPASSUNG.get(rolle, 0)
        if wert < richtwert - 0.1:
            verbesserungen.append(_tipp(
                18 + (richtwert - wert) * 100, "🤝", "Teamfights",
                f"Du warst an {round(wert * 100)}% der Kills deines Teams beteiligt - für {wer} sind "
                f"~{round(richtwert * 100)}% üblich. Wenn ein Objective oder Fight ansteht, lieber früher "
                f"dazustoßen als noch eine Welle zu farmen.",
            ))
        elif wert >= richtwert + 0.1:
            staerken.append(_tipp(
                (wert - richtwert) * 100, "🤝", "Immer dabei",
                f"An {round(wert * 100)}% der Team-Kills beteiligt (Schnitt der {wer}: ~{round(richtwert * 100)}%).",
            ))

    if "damage_share" in vergleich:
        wert, mindest = vergleich["damage_share"]
        if wert < mindest - 0.03:
            verbesserungen.append(_tipp(
                12 + (mindest - wert) * 100, "💥", "Schaden",
                f"Nur {round(wert * 100)}% des Team-Schadens an Champions - als {ROLLEN_EINZAHL.get(rolle, 'Spieler')} "
                f"sind mindestens ~{round(mindest * 100)}% üblich. Such in Fights Positionen, aus denen du "
                f"länger Schaden machen kannst, statt nur den ersten Trade mitzunehmen.",
            ))
        elif wert >= 0.3:
            staerken.append(_tipp(
                (wert - 0.3) * 100 + 10, "💥", "Hauptschadensquelle",
                f"{round(wert * 100)}% des gesamten Team-Schadens kamen von dir.",
            ))


def _pruefe_tode(k, verbesserungen, staerken):
    tode = sorted(d["timestamp"] for d in k["death_positions"])
    minuten = k["minuten"]
    if len(tode) <= 2 and minuten >= 20:
        staerken.append(_tipp(
            12 - len(tode) * 3, "🛡️", "Sauber gespielt",
            f"Nur {len(tode)} Tod{'e' if len(tode) != 1 else ''} in {round(minuten)} Minuten.",
        ))
    if len(tode) >= 5:
        # Dichtestes 5-Minuten-Fenster - Tode häufen sich oft in einer Phase (z.B. Midgame)
        beste = (0, 0, 0)
        for i, start in enumerate(tode):
            anzahl = sum(1 for t in tode[i:] if t - start <= 300_000)
            if anzahl > beste[0]:
                beste = (anzahl, start, max(t for t in tode[i:] if t - start <= 300_000))
        if beste[0] >= 3:
            verbesserungen.append(_tipp(
                20 + beste[0] * 3, "💀", "Tode gehäuft",
                f"{beste[0]} deiner {len(tode)} Tode fielen zwischen {format_game_time(beste[1])} und "
                f"{format_game_time(beste[2])}. In so einer Phase hilft es, nach einem Tod erst mit dem "
                f"Team zu gruppieren, statt allein wieder nach vorne zu gehen.",
            ))

    # Tod kurz bevor der Gegner ein Objective holt
    mein_team = k["mein_team_id"]
    vor_objective = []
    for t in tode:
        for obj in k["timeline_extra"].get("objective_kills", []):
            if str(obj.get("killer_team_id")) == str(mein_team):
                continue
            if 0 < obj["timestamp"] - t <= 90_000:
                vor_objective.append((t, OBJECTIVE_NAMEN.get(obj.get("monster_type"), "Objective")))
                break
    if vor_objective:
        zeiten = ", ".join(f"{format_game_time(t)} ({name})" for t, name in vor_objective[:3])
        verbesserungen.append(_tipp(
            22 + len(vor_objective) * 6, "🐉", "Tode vor Objectives",
            f"{len(vor_objective)}x bist du gestorben und kurz danach hat der Gegner ein Objective "
            f"geholt: {zeiten}. Etwa eine Minute vor einem Objective lieber Vision setzen und mit dem "
            f"Team hingehen, statt allein im Fluss unterwegs zu sein.",
        ))


def _pruefe_lane_gold(k, verbesserungen, staerken):
    lane = k["lane_vergleich"]
    if not lane or not lane.get("gold_verlauf") or len(lane["gold_verlauf"]) < 5:
        return
    verlauf = lane["gold_verlauf"]
    gegner = f"{lane['gegner']['riot_name']} ({lane['gegner']['champion']})"
    tiefster = min(verlauf, key=lambda f: f["diff"])
    hoechster = max(verlauf, key=lambda f: f["diff"])
    ende = verlauf[-1]["diff"]

    if tiefster["diff"] <= -1500:
        start = next(f for f in verlauf if f["diff"] <= -1000)
        tod_davor = [
            d["timestamp"] for d in k["death_positions"]
            if start["minute"] * 60_000 - 180_000 <= d["timestamp"] <= start["minute"] * 60_000
        ]
        grund = f", kurz nach deinem Tod bei {format_game_time(tod_davor[-1])}" if tod_davor else ""
        verbesserungen.append(_tipp(
            20 + abs(tiefster["diff"]) / 150, "📉", "Lane-Rückstand",
            f"Gegen {gegner} lagst du bei Minute {tiefster['minute']} mit {_gold(tiefster['diff'])} Gold hinten. "
            f"Der Rückstand begann um Minute {start['minute']}{grund}. In so einer Lage lieber sicher farmen "
            f"und auf Hilfe spielen, statt den Rückstand mit riskanten Trades aufholen zu wollen.",
        ))
    if hoechster["diff"] >= 1500 and ende <= hoechster["diff"] - 2000:
        verbesserungen.append(_tipp(
            18 + (hoechster["diff"] - ende) / 200, "📉", "Vorsprung verspielt",
            f"Bei Minute {hoechster['minute']} warst du {_gold(hoechster['diff'])} Gold vor {gegner}, "
            f"am Ende {'nur noch ' + _gold(ende) + ' vorne' if ende > 0 else _gold(ende) + ' hinten'}. Mit einem "
            f"Vorsprung lohnt es sich, ihn in Türme oder Objectives umzuwandeln, statt weiter Kills zu suchen.",
        ))
    if hoechster["diff"] >= 1500:
        staerken.append(_tipp(
            hoechster["diff"] / 200, "📈", "Lane gewonnen",
            f"Bis zu {_gold(hoechster['diff'])} Gold Vorsprung auf {gegner} (Minute {hoechster['minute']}).",
        ))


def _pruefe_objectives(k, verbesserungen, staerken):
    mein_team = str(k["mein_team_id"])
    zaehlung = {"eigen": {}, "gegner": {}}
    for obj in k["timeline_extra"].get("objective_kills", []):
        name = OBJECTIVE_NAMEN.get(obj.get("monster_type"))
        if not name:
            continue
        seite = "eigen" if str(obj.get("killer_team_id")) == mein_team else "gegner"
        zaehlung[seite][name] = zaehlung[seite].get(name, 0) + 1
    eigen, gegner = sum(zaehlung["eigen"].values()), sum(zaehlung["gegner"].values())

    def aufzaehlen(werte):
        return ", ".join(f"{anzahl}x {name}" for name, anzahl in werte.items()) or "keine"

    ist_jungler = k["role"] == "JUNGLE"
    if gegner - eigen >= 3:
        verbesserungen.append(_tipp(
            (24 if ist_jungler else 12) + (gegner - eigen) * 2, "🐲", "Objectives",
            f"Der Gegner holte {aufzaehlen(zaehlung['gegner'])}, dein Team {aufzaehlen(zaehlung['eigen'])}. "
            + ("Als Jungler lohnt es sich, die Spawn-Zeiten im Blick zu behalten und 60 Sekunden vorher "
               "mit Priorität in den Lanes dort zu sein."
               if ist_jungler else
               "Achte auf die Spawn-Zeiten und schieb deine Welle rechtzeitig, damit du beim Objective dabei sein kannst."),
        ))
    elif eigen - gegner >= 3:
        staerken.append(_tipp(
            (eigen - gegner) * 2 + (6 if ist_jungler else 0), "🐲", "Objective-Kontrolle",
            f"Dein Team holte {aufzaehlen(zaehlung['eigen'])}, der Gegner nur {aufzaehlen(zaehlung['gegner'])}.",
        ))


def _rune_namen(version):
    namen, baeume = {}, {}
    for baum in get_rune_trees(version).values():
        baeume[baum["id"]] = baum["name"]
        for slot in baum["slots"]:
            for rune in slot["runes"]:
                namen[rune["id"]] = rune["name"]
    return namen, baeume


def _pruefe_runen(k, cur, verbesserungen):
    styles = (k["perks"] or {}).get("styles") or []
    if len(styles) < 2 or not styles[0].get("selections"):
        return
    mein = (styles[0]["selections"][0]["perk"], styles[1]["style"])
    cur.execute("SELECT win, perks FROM participants WHERE champion = %s;", (k["champion"],))
    varianten, spiele = berechne_runen_varianten(cur.fetchall())
    if not varianten or spiele < MIN_SPIELE_VERGLEICH:
        return
    top = varianten[0]
    top_key = (top["perks"]["styles"][0]["selections"][0]["perk"], top["perks"]["styles"][1]["style"])
    if top_key == mein or top["anteil"] < 40:
        return
    namen, baeume = _rune_namen(k["version"])
    eigene = next((v for v in varianten
                   if (v["perks"]["styles"][0]["selections"][0]["perk"], v["perks"]["styles"][1]["style"]) == mein), None)
    if eigene and eigene["winrate"] >= top["winrate"] - 2:
        return  # die eigene Kombination läuft genauso gut oder besser - kein Grund zu wechseln
    eigene_text = f" Deine Kombination: {eigene['winrate']}% Winrate in {eigene['games']} Spielen." if eigene else ""
    verbesserungen.append(_tipp(
        10 + top["anteil"] / 10, "🔮", "Runen",
        f"Du hast {namen.get(mein[0], '?')} + {baeume.get(mein[1], '?')} gespielt. {top['anteil']}% der "
        f"{k['champion']}-Spiele in der Datenbank nutzen {namen.get(top_key[0], '?')} + {baeume.get(top_key[1], '?')} "
        f"({top['winrate']}% Winrate in {top['games']} Spielen).{eigene_text}",
    ))


def _pruefe_skills(k, cur, verbesserungen):
    erreicht, maximiert = skill_prioritaet(k["skill_order"])
    if maximiert < 1:
        return  # Spiel zu kurz, um überhaupt einen Skill zu maximieren
    cur.execute(
        "SELECT skill_order FROM participants WHERE champion = %s AND skill_order IS NOT NULL AND match_id <> %s;",
        (k["champion"], k["match_id"]),
    )
    erste = {}
    for (order,) in cur.fetchall():
        e, m = skill_prioritaet(order)
        if m >= 1:
            erste[e[0]] = erste.get(e[0], 0) + 1
    gesamt = sum(erste.values())
    if gesamt < MIN_SPIELE_VERGLEICH:
        return
    top, anzahl = max(erste.items(), key=lambda kv: kv[1])
    anteil = round(anzahl / gesamt * 100)
    if top != erreicht[0] and anteil >= 60:
        verbesserungen.append(_tipp(
            8 + anteil / 10, "📘", "Skill-Reihenfolge",
            f"Du hast zuerst {SKILL_BUCHSTABE[erreicht[0]]} maximiert - in {anteil}% der {k['champion']}-Spiele "
            f"in der Datenbank wird zuerst {SKILL_BUCHSTABE[top]} maximiert ({gesamt} Spiele).",
        ))


def _erstes_fertiges_item(item_timeline, item_info):
    for kauf in sorted(item_timeline or [], key=lambda x: x["timestamp"]):
        if kauf.get("itemId") and _fertige_items([kauf["itemId"]], item_info):
            return kauf["timestamp"], kauf["itemId"]
    return None, None


def _pruefe_item_timing(k, cur, verbesserungen, staerken):
    try:
        item_info = _get_item_info()
    except Exception:
        return
    zeit, item_id = _erstes_fertiges_item(k["item_timeline"], item_info)
    if zeit is None:
        return
    cur.execute(
        "SELECT item_timeline FROM participants WHERE champion = %s AND item_timeline IS NOT NULL AND match_id <> %s;",
        (k["champion"], k["match_id"]),
    )
    andere = [t for t in (_erstes_fertiges_item(tl, item_info)[0] for (tl,) in cur.fetchall()) if t]
    if len(andere) < MIN_SPIELE_ITEM_TIMING:
        return
    schnitt = median(andere)
    name = item_info.get(item_id, {}).get("name") or "Item"
    if zeit > schnitt + 120_000:
        verbesserungen.append(_tipp(
            12 + (zeit - schnitt) / 30_000, "⏱️", "Erstes Item",
            f"Dein erstes fertiges Item ({name}) kam bei {format_game_time(zeit)} - {k['champion']}-Spieler "
            f"in der Datenbank haben ihr erstes im Mittel bei {format_game_time(schnitt)} ({len(andere)} Spiele). "
            f"Mehr Farm und Recalls mit genug Gold für eine volle Komponente beschleunigen das.",
        ))
    elif zeit < schnitt - 60_000:
        staerken.append(_tipp(
            (schnitt - zeit) / 30_000, "⏱️", "Schnelles erstes Item",
            f"{name} schon bei {format_game_time(zeit)} - im Mittel dauert das bis {format_game_time(schnitt)}.",
        ))


# ---------------------------------------------------------------------------------------

def erstelle_tipps(cur, *, puuid, match_id, tier, tier_bekannt, teams, lane_vergleich, death_positions,
                   timeline_extra, item_timeline, skill_order, version):
    """Gibt {"verbesserungen", "staerken", "referenz"} zurück - oder None, wenn das Spiel
    nicht gefunden wird."""
    cur.execute(EINZEL_QUERY, (puuid, match_id))
    row = cur.fetchone()
    if row is None:
        return None
    werte, vergleich = match_metrics(row, tier)
    mein_team_id = next(
        (team_id for team_id, seite in (teams or {}).items() for p in seite if p["puuid"] == puuid), None
    )
    k = {
        "match_id": match_id, "champion": werte["champion"], "role": werte["role"],
        "vergleich": vergleich, "minuten": werte["duration_seconds"] / 60,
        "tier_name": tier.title(), "perks": werte["perks"], "skill_order": skill_order or [],
        "death_positions": death_positions or [], "timeline_extra": timeline_extra or {},
        "item_timeline": item_timeline or [], "lane_vergleich": lane_vergleich,
        "mein_team_id": mein_team_id, "version": version,
    }

    verbesserungen, staerken = [], []
    _pruefe_richtwerte(k, verbesserungen, staerken)
    if mein_team_id is not None:
        _pruefe_tode(k, verbesserungen, staerken)
        _pruefe_objectives(k, verbesserungen, staerken)
    _pruefe_lane_gold(k, verbesserungen, staerken)
    for pruefung in (_pruefe_runen, _pruefe_skills):
        try:
            pruefung(k, cur, verbesserungen)
        except Exception:
            pass  # Champion-Vergleiche sind Zusatz - fehlende/ungewöhnliche Daten dürfen nie die Seite kosten
    try:
        _pruefe_item_timing(k, cur, verbesserungen, staerken)
    except Exception:
        pass

    verbesserungen.sort(key=lambda t: t["prioritaet"], reverse=True)
    staerken.sort(key=lambda t: t["prioritaet"], reverse=True)
    rolle = ROLLEN_TEXT.get(werte["role"])
    referenz = (
        f"Richtwerte für {tier.title()}{'-' + rolle if rolle else ''}"
        + (" (dein aktueller Solo/Duo-Rang)" if tier_bekannt else " (Rang unbekannt, Gold als Referenz)")
    )
    return {
        "verbesserungen": verbesserungen[:MAX_VERBESSERUNGEN],
        "staerken": staerken[:MAX_STAERKEN],
        "referenz": referenz,
    }
