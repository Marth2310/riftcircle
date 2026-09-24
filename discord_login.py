"""Discord-Login (OAuth2, nur Scope "identify": Name + Avatar, keine E-Mail, keine Server)."""
import urllib.parse

import requests

from riot_assets import REQUEST_TIMEOUT

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
USER_URL = "https://discord.com/api/users/@me"


def login_url(client_id, redirect_uri, state):
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "scope": "identify",
        "redirect_uri": redirect_uri,
        "state": state,
        # Wer RiftCircle schon einmal zugestimmt hat, muss das nicht bei jedem Login erneut tun
        "prompt": "none",
    })


def avatar_url(discord_id, avatar_hash):
    if avatar_hash:
        return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
    # Ohne eigenes Bild: Discords Standard-Avatar (Index nach Discords Regel für neue Usernamen)
    return f"https://cdn.discordapp.com/embed/avatars/{(int(discord_id) >> 22) % 6}.png"


def hole_discord_nutzer(client_id, client_secret, code, redirect_uri):
    """Tauscht den Code aus dem Login-Redirect gegen ein Token und liest damit den Nutzer.
    None, wenn Discord etwas davon ablehnt (abgelaufener Code, falsches Secret, ...)."""
    try:
        token_resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )
        if token_resp.status_code != 200:
            return None
        token = token_resp.json().get("access_token")
        if not token:
            return None
        user_resp = requests.get(USER_URL, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
        if user_resp.status_code != 200:
            return None
        daten = user_resp.json()
    except (requests.RequestException, ValueError):
        return None
    return {
        "id": daten["id"],
        # global_name = frei wählbarer Anzeigename, username = eindeutiger Login-Name
        "name": daten.get("global_name") or daten["username"],
        "avatar": avatar_url(daten["id"], daten.get("avatar")),
    }
