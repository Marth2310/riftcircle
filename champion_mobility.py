# Best-effort-Liste von Champions OHNE aktive Fluchtfähigkeit (kein Dash/Blink/Leap, der
# sie aus Gefahr herausbringt - reine Bewegungstempo-Boosts zählen hier nicht als Escape).
# Nicht erschöpfend/wissenschaftlich - Grenzfälle (z.B. Champions mit Schild statt
# Distanzgewinn) sind Ermessenssache. Neuere Champions nach dem Wissensstand fehlen ggf. -
# gerne erweitern/korrigieren. Namen entsprechen Riots championName (Data-Dragon-Schreibweise).
CHAMPIONS_OHNE_ESCAPE = {
    "Annie", "Anivia", "Ashe", "Brand", "Caitlyn", "Cassiopeia", "Draven",
    "Heimerdinger", "Janna", "Jhin", "Karthus", "KogMaw", "Lux", "Malzahar",
    "MissFortune", "Morgana", "Nasus", "Nunu", "Senna", "Sion", "Soraka",
    "Swain", "Twitch", "Varus", "Veigar", "Velkoz", "Viktor", "Xerath",
    "Yuumi", "Ziggs", "Zyra",
}


def hat_escape(champion):
    """True, falls der Champion (best effort) eine aktive Fluchtfähigkeit hat."""
    return champion not in CHAMPIONS_OHNE_ESCAPE
