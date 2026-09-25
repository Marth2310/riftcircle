"""Zusätzliche Auswertungen für die Profilseite: Rang-Verlauf, Stats pro Rolle, Duo-Partner."""

# Reihenfolge der Tiers für eine durchgehende Punkteskala (je Tier 400 Punkte = 4 Divisionen à
# 100 LP). Master/Grandmaster/Challenger teilen sich eine LP-Skala ohne Divisionen.
TIER_REIHENFOLGE = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND"]
APEX_TIERS = ["MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISIONEN = {"IV": 0, "III": 1, "II": 2, "I": 3}
QUEUES = {"solo": "Solo/Duo", "flex": "Flex"}

ROLLEN = [
    ("TOP", "Top", "top"), ("JUNGLE", "Jungle", "jungle"), ("MIDDLE", "Mid", "middle"),
    ("BOTTOM", "ADC", "bottom"), ("UTILITY", "Support", "utility"),
]
MIN_SPIELE_DUO = 2
MAX_DUO_PARTNER = 5


def rang_punkte(tier, division, lp):
    """Tier + Division + LP -> eine Zahl, damit sich der Rang als Linie zeichnen lässt
    (Gold IV 0 LP = 1200, Gold I 50 LP = 1550, Master 120 LP = 2920)."""
    if tier in APEX_TIERS:
        return len(TIER_REIHENFOLGE) * 400 + lp
    if tier not in TIER_REIHENFOLGE:
        return None
    return TIER_REIHENFOLGE.index(tier) * 400 + DIVISIONEN.get(division, 0) * 100 + lp


def rang_speichern(cur, puuid, ranks):
    """Tages-Stand je Queue (ein Eintrag pro Tag, spätere Aufrufe am selben Tag überschreiben)."""
    for queue, rang in ranks.items():
        if not rang or not rang.get("tier"):
            continue
        cur.execute("""
            INSERT INTO rang_verlauf (puuid, queue, tier, division, lp) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (puuid, queue, tag) DO UPDATE SET
                tier = EXCLUDED.tier, division = EXCLUDED.division, lp = EXCLUDED.lp;
        """, (puuid, queue, rang["tier"], rang.get("rank"), rang.get("lp", 0)))


def rang_verlauf(cur, puuid, tage=90):
    """Für das Diagramm: {"labels": [...], "reihen": [{"queue", "name", "punkte", "text"}]} oder
    None ohne gespeicherte Stände. Tage ohne Aufruf fehlen einfach (keine erfundenen Werte)."""
    cur.execute("""
        SELECT queue, tag, tier, division, lp FROM rang_verlauf
        WHERE puuid = %s AND tag >= CURRENT_DATE - %s
        ORDER BY tag;
    """, (puuid, tage))
    zeilen = cur.fetchall()
    if not zeilen:
        return None
    tage_sortiert = sorted({tag for _, tag, *_ in zeilen})
    labels = [t.strftime("%d.%m.") for t in tage_sortiert]
    reihen = []
    for queue, name in QUEUES.items():
        werte = {tag: (tier, division, lp) for q, tag, tier, division, lp in zeilen if q == queue}
        if not werte:
            continue
        punkte, text = [], []
        for t in tage_sortiert:
            if t in werte:
                tier, division, lp = werte[t]
                punkte.append(rang_punkte(tier, division, lp))
                apex = tier in APEX_TIERS
                text.append(f"{tier.title()}{'' if apex else ' ' + (division or '')} · {lp} LP")
            else:
                punkte.append(None)
                text.append(None)
        reihen.append({"queue": queue, "name": name, "punkte": punkte, "text": text})
    return {"labels": labels, "reihen": reihen, "tage": len(tage_sortiert)}


def tier_achse():
    """Beschriftung der y-Achse: Punktwert -> "Gold IV" usw."""
    achse = {}
    for i, tier in enumerate(TIER_REIHENFOLGE):
        for division, stufe in DIVISIONEN.items():
            achse[i * 400 + stufe * 100] = f"{tier.title()} {division}"
    achse[len(TIER_REIHENFOLGE) * 400] = "Master"
    return achse


def rollen_statistik(cur, puuid):
    """Spiele, Winrate und KDA je Position über alle gespeicherten Spiele (ARAM u.ä. ohne
    Position zählen nicht mit)."""
    cur.execute("""
        SELECT role, COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END), SUM(kills), SUM(deaths), SUM(assists)
        FROM participants
        WHERE puuid = %s AND role IN ('TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY')
        GROUP BY role;
    """, (puuid,))
    werte = {role: rest for role, *rest in cur.fetchall()}
    gesamt = sum(v[0] for v in werte.values())
    ergebnis = []
    for role, name, icon in ROLLEN:
        if role not in werte:
            ergebnis.append({"role": role, "name": name, "icon": icon, "spiele": 0})
            continue
        spiele, siege, k, d, a = werte[role]
        ergebnis.append({
            "role": role, "name": name, "icon": icon, "spiele": spiele, "siege": siege,
            "winrate": round(siege / spiele * 100),
            "kda": round((k + a) / d, 2) if d else float(k + a),
            "anteil": round(spiele / gesamt * 100) if gesamt else 0,
        })
    return ergebnis if gesamt else []


def duo_partner(cur, puuid):
    """Mit wem spielt der Spieler am häufigsten im selben Team, und wie oft gewinnen sie dann?
    Quellen: andere gespeicherte Teilnehmer desselben Matches mit gleichem Ergebnis (= selbes
    Team) plus die Team-Aufstellungen bereits geöffneter Matches (alle 10 Spieler)."""
    cur.execute("""
        WITH meine AS (
            SELECT match_id, win FROM participants WHERE puuid = %(puuid)s
        ),
        partner AS (
            SELECT p.puuid, p.match_id, p.win
            FROM participants p JOIN meine m ON m.match_id = p.match_id AND p.win = m.win
            WHERE p.puuid <> %(puuid)s
            UNION
            SELECT sp->>'puuid', mt.match_id, m.win
            FROM meine m
            JOIN (SELECT match_id, team_lineup FROM matches WHERE jsonb_typeof(team_lineup) = 'object') mt
                ON mt.match_id = m.match_id,
                 jsonb_each(mt.team_lineup) AS seite(team, spieler),
                 jsonb_array_elements(CASE WHEN jsonb_typeof(seite.spieler) = 'array'
                                           THEN seite.spieler ELSE '[]'::jsonb END) AS sp
            WHERE sp->>'puuid' <> %(puuid)s AND (sp->>'win')::boolean = m.win
        )
        SELECT pa.puuid,
               COALESCE(b.riot_name, pl.riot_name) AS name, COALESCE(b.riot_tag, pl.riot_tag) AS tag,
               COUNT(DISTINCT pa.match_id) AS spiele,
               COUNT(DISTINCT pa.match_id) FILTER (WHERE pa.win) AS siege
        FROM partner pa
        LEFT JOIN bekannte_spieler b ON b.puuid = pa.puuid
        LEFT JOIN players pl ON pl.puuid = pa.puuid
        GROUP BY pa.puuid, name, tag
        HAVING COUNT(DISTINCT pa.match_id) >= %(min_spiele)s
        ORDER BY spiele DESC, siege DESC
        LIMIT %(limit)s;
    """, {"puuid": puuid, "min_spiele": MIN_SPIELE_DUO, "limit": MAX_DUO_PARTNER * 3})
    partner = []
    for p, name, tag, spiele, siege in cur.fetchall():
        # Anonyme Challenger-Import-Teilnehmer ohne echten Namen lassen sich nicht verlinken
        if not name or not tag or name.startswith("Meta-"):
            continue
        partner.append({
            "puuid": p, "name": name, "tag": tag, "spiele": spiele, "siege": siege,
            "niederlagen": spiele - siege, "winrate": round(siege / spiele * 100),
        })
    return partner[:MAX_DUO_PARTNER]
