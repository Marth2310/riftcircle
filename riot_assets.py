import requests

# Data Dragon Patch-Version wird nur einmal pro Prozess-Laufzeit abgefragt (ändert sich max.
# alle paar Wochen mit einem neuen Patch) statt bei jedem Dashboard-Aufruf neu.
_ddragon_version_cache = None


def get_ddragon_version():
    global _ddragon_version_cache
    if _ddragon_version_cache is None:
        versions = requests.get("https://ddragon.leagueoflegends.com/api/versions.json").json()
        _ddragon_version_cache = versions[0]
    return _ddragon_version_cache


def champion_icon_url(champion, version=None):
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png"


def champion_splash_url(champion):
    """Splash-Art (Skin 0 = Standard-Skin, als .jpg) - nicht patch-versioniert, für Hintergrundbilder."""
    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion}_0.jpg"


def item_icon_url(item_id, version=None):
    """None für leere Item-Slots (item_id 0), sonst die Data-Dragon-Icon-URL."""
    if not item_id:
        return None
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/item/{item_id}.png"


def get_summoner_icon_id(puuid, headers):
    """Holt die Profile-Icon-ID des Spielers über die Summoner-v4-API."""
    url = f"https://euw1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    data = requests.get(url, headers=headers).json()
    return data.get("profileIconId")


def summoner_icon_url(profile_icon_id, version=None):
    version = version or get_ddragon_version()
    return f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{profile_icon_id}.png"
