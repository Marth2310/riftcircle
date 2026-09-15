# --- Elo-Richtwerte (Community-Schätzungen, grob orientiert an Aggregator-Seiten
#     wie U.GG/OP.GG - keine offiziellen Riot-Daten, Werte können abweichen!) ---
# Werte = ungefährer Durchschnitt pro Minute in dieser Elo
LANE_BENCHMARKS = {  # TOP, MIDDLE, BOTTOM
    "IRON": {"cs": 4.0, "vision": 0.45}, "BRONZE": {"cs": 4.3, "vision": 0.48},
    "SILVER": {"cs": 5.3, "vision": 0.54}, "GOLD": {"cs": 6.3, "vision": 0.66},
    "PLATINUM": {"cs": 7.3, "vision": 0.72}, "EMERALD": {"cs": 7.8, "vision": 0.77},
    "DIAMOND": {"cs": 8.3, "vision": 0.85}, "MASTER": {"cs": 9.0, "vision": 0.90},
}
JUNGLE_BENCHMARKS = {
    "IRON": {"cs": 3.5, "vision": 0.65}, "BRONZE": {"cs": 3.8, "vision": 0.70},
    "SILVER": {"cs": 4.0, "vision": 0.75}, "GOLD": {"cs": 4.5, "vision": 0.82},
    "PLATINUM": {"cs": 5.0, "vision": 0.90}, "EMERALD": {"cs": 5.5, "vision": 0.95},
    "DIAMOND": {"cs": 6.0, "vision": 1.00}, "MASTER": {"cs": 6.5, "vision": 1.05},
}
SUPPORT_BENCHMARKS = {
    "IRON": {"vision": 0.71}, "BRONZE": {"vision": 0.85}, "SILVER": {"vision": 1.07},
    "GOLD": {"vision": 1.35}, "PLATINUM": {"vision": 1.52}, "EMERALD": {"vision": 1.80},
    "DIAMOND": {"vision": 2.05}, "MASTER": {"vision": 2.30},
}
# KDA ist rollenunabhängig, daher eine einzelne Tabelle (gilt auch für ARAM)
KDA_BENCHMARKS = {
    "IRON": 1.8, "BRONZE": 2.0, "SILVER": 2.2, "GOLD": 2.5,
    "PLATINUM": 2.8, "EMERALD": 3.0, "DIAMOND": 3.3, "MASTER": 3.6,
}
# Kill-Participation = (kills+assists) / team_kills. Steigt tendenziell leicht mit der Elo,
# da Teamfights/Objectives koordinierter gespielt werden.
KILL_PARTICIPATION_BENCHMARKS = {
    "IRON": 0.50, "BRONZE": 0.52, "SILVER": 0.55, "GOLD": 0.58,
    "PLATINUM": 0.60, "EMERALD": 0.62, "DIAMOND": 0.65, "MASTER": 0.68,
}
# Damage-Share hängt stark von Champion/Rolle ab (Tank/Support naturgemäß niedrig),
# daher kein Elo-Richtwert, sondern ein grober Mindestwert für Schadens-Rollen (TOP/MID/BOT/JUNGLE).
DAMAGE_SHARE_MIN_NON_SUPPORT = 0.20
# Objective-Teilnahme (Turm-Takedowns + gestohlene Objectives) ist stark spiel-/teamabhängig,
# daher ebenfalls kein Elo-Richtwert, sondern ein grober Mindestwert pro Spiel (gilt nicht für ARAM,
# da es dort keine Dragons/Baron/Herald gibt).
OBJECTIVE_PARTICIPATION_MIN = 1


def table_for_role(role):
    """Liefert die passende Rollen-Benchmark-Tabelle, oder None (z.B. ARAM)."""
    if role == "JUNGLE":
        return JUNGLE_BENCHMARKS
    if role in ("TOP", "MIDDLE", "BOTTOM"):
        return LANE_BENCHMARKS
    if role == "UTILITY":
        return SUPPORT_BENCHMARKS
    return None


def normalize_tier(tier):
    """Bildet CHALLENGER/GRANDMASTER auf MASTER ab, da die Tabellen dort enden."""
    if tier in ("GRANDMASTER", "CHALLENGER"):
        return "MASTER"
    return tier


def closest_tier(value, benchmark_table, stat):
    """Findet die Elo, deren Richtwert am nächsten am tatsächlichen Wert liegt."""
    return min(benchmark_table, key=lambda tier: abs(benchmark_table[tier][stat] - value))


def closest_tier_flat(value, benchmark_dict):
    """Wie closest_tier, aber für flache Tabellen (Tier -> Zahl statt Tier -> Dict)."""
    return min(benchmark_dict, key=lambda tier: abs(benchmark_dict[tier] - value))
