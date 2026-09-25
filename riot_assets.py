import random

import requests

# Timeout für alle Riot-/Data-Dragon-Calls: ohne das wartet requests im Zweifel unbegrenzt -
# bei nur einem Gunicorn-Worker (siehe Procfile) reicht dann ein einziger hängender Call, um
# die komplette App für alle einzufrieren (genau das ist am 2026-09-18 live passiert).
REQUEST_TIMEOUT = 10

# Data Dragon Patch-Version wird nur einmal pro Prozess-Laufzeit abgefragt (ändert sich max.
# alle paar Wochen mit einem neuen Patch) statt bei jedem Dashboard-Aufruf neu. Fallback-Wert
# für den (seltenen) Fall, dass Data Dragon selbst mal kurz nicht erreichbar ist - ohne den
# würde JEDE Seite abstürzen, weil praktisch jede Icon-/Splash-URL eine Version braucht.
_ddragon_version_cache = None
_FALLBACK_VERSION = "14.19.1"

# champion_key -> (version, Champion-JSON aus Data Dragon) - Quelle für Skins und Spells
_champion_daten_cache = {}


def get_ddragon_version():
    global _ddragon_version_cache
    if _ddragon_version_cache is None:
        try:
            resp = requests.get(
                "https://ddragon.leagueoflegends.com/api/versions.json", timeout=REQUEST_TIMEOUT
            )
            versions = resp.json()
            _ddragon_version_cache = versions[0] if isinstance(versions, list) and versions else _FALLBACK_VERSION
        except (requests.RequestException, ValueError):
            _ddragon_version_cache = _FALLBACK_VERSION
    return _ddragon_version_cache


def champion_icon_url(champion, version=None):
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png"


def champion_splash_url(champion, skin_num=0):
    """Splash-Art (Skin 0 = Standard-Skin, als .jpg) - nicht patch-versioniert, für Hintergrundbilder."""
    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion}_{skin_num}.jpg"


def _get_champion_daten(champion):
    """Detail-JSON eines Champions (Skins, Spells, ...) - einmal pro Patch-Version gecacht.
    None, wenn Data Dragon den Champion nicht liefert."""
    version = get_ddragon_version()
    cached = _champion_daten_cache.get(champion)
    if cached is None or cached[0] != version:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion/{champion}.json"
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None
        _champion_daten_cache[champion] = (version, resp.json()["data"][champion])
    return _champion_daten_cache[champion][1]


def _get_base_skin_numbers(champion):
    """Skin-Nummern eines Champions OHNE Chromas - Chromas (z.B. "Bullet Angel Kai'Sa
    (Ruby)") teilen sich das Splash-Art ihres Basis-Skins und liefern unter ihrer eigenen
    Nummer einen 403, taugen also nicht für zufällige Hintergrundbilder."""
    daten = _get_champion_daten(champion)
    if not daten:
        return [0]
    return [s["num"] for s in daten["skins"] if "(" not in s["name"]] or [0]


def champion_skin_splashes(champion, anzahl=8):
    """Bis zu `anzahl` Splash-Arts verschiedener Skins in zufälliger Reihenfolge - für
    Hintergründe, die zwischen den Skins eines Champions wechseln."""
    nums = list(_get_base_skin_numbers(champion))
    random.shuffle(nums)
    return [champion_splash_url(champion, n) for n in nums[:anzahl]]


def get_champion_spells(champion):
    """Q/W/E/R des Champions als [{"name", "icon"}] (Index 0 = Q), None falls nicht ladbar."""
    daten = _get_champion_daten(champion)
    if not daten or len(daten.get("spells", [])) < 4:
        return None
    version = get_ddragon_version()
    return [
        {
            "name": s["name"],
            "icon": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/spell/{s['image']['full']}",
        }
        for s in daten["spells"][:4]
    ]


def random_champion_splash_url(champion):
    """Zufälliges Splash-Art unter den Basis-Skins eines Champions - für den transparenten
    Profil-Hintergrund, der bei jedem Seitenaufruf einen anderen Skin desselben Champions zeigt."""
    skin_num = random.choice(_get_base_skin_numbers(champion))
    return champion_splash_url(champion, skin_num)


def item_icon_url(item_id, version=None):
    """None für leere Item-Slots (item_id 0), sonst die Data-Dragon-Icon-URL."""
    if not item_id:
        return None
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"


def get_summoner_icon_id(puuid, headers):
    """Holt die Profile-Icon-ID des Spielers über die Summoner-v4-API."""
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    data = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT).json()
    return data.get("profileIconId")


def summoner_icon_url(profile_icon_id, version=None):
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{profile_icon_id}.png"
