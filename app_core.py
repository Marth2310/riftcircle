"""Grundgerüst: Flask-App, Konfiguration aus der Umgebung, Riot-Header, Login-/Session-
Einstellungen und das Besucher-Tracking. Alle anderen Module registrieren ihre Routen hier."""
import hashlib
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, request, session

from db import get_connection


load_dotenv()
API_KEY = os.environ["RIOT_API_KEY"]
headers = {"X-Riot-Token": API_KEY}
# Öffentliche Adresse der Seite für Links in Discord-Nachrichten (dort gehen nur absolute URLs)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://riftcircle.com").rstrip("/")
# Schaltet /stats/<secret> frei und salzt den Besucher-Hash - ohne gesetztes Secret bleibt
# Tracking einfach aus (z.B. lokal, wenn man sich damit nicht beschäftigen will).
ANALYTICS_SECRET = os.environ.get("ANALYTICS_SECRET", "")

app = Flask(__name__)

# Discord-Login: die Client-ID ist öffentlich (steht ohnehin in jeder Login-URL), Secret und
# Session-Schlüssel kommen aus der Umgebung. Fehlt eins davon, bleibt der Login einfach aus.
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "1552678283215110296")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or None
LOGIN_AKTIV = bool(DISCORD_CLIENT_SECRET and app.secret_key)
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SAMESITE="Lax",
    # Lokal läuft die App über http, live nur über https
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_DEBUG", "1") != "1",
)


@app.context_processor
def _konto_kontext():
    """Für den Anmelde-Knopf oben rechts auf jeder Seite - bewusst nur aus der Session (Name,
    Avatar), ohne DB-Abfrage bei jedem Seitenaufruf."""
    return {"login_aktiv": LOGIN_AKTIV, "angemeldet": session.get("nutzer") if LOGIN_AKTIV else None}


@app.template_filter("tausender")
def tausender(zahl):
    """1041 -> "1.041" (deutsche Tausendertrennung)."""
    return f"{zahl:,}".replace(",", ".")


BOT_USER_AGENT_WOERTER =("bot", "spider", "crawler", "slurp", "bingpreview", "facebookexternalhit")


@app.after_request
def _seite_tracken(response):
    """Loggt echte Seitenaufrufe fürs eigene Statistik-Dashboard - keine rohe IP, nur ein
    gesalzener Hash aus IP+User-Agent (kann nicht ohne das Secret auf die IP zurückgerechnet
    werden). Nur erfolgreiche GET-Seitenaufrufe zählen, keine Formular-POSTs, kein Static,
    keine Bots, und die Statistikseite selbst zählt sich nicht mit."""
    if (ANALYTICS_SECRET and request.method == "GET" and response.status_code == 200
            and request.endpoint not in (None, "static", "seite_statistik", "favicon", "spieler_suche")):
        user_agent = (request.headers.get("User-Agent") or "").lower()
        if not any(wort in user_agent for wort in BOT_USER_AGENT_WOERTER):
            besucher_hash = hashlib.sha256(
                (ANALYTICS_SECRET + (request.remote_addr or "") + user_agent).encode()
            ).hexdigest()[:16]
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO page_views (path, visitor_hash) VALUES (%s, %s);",
                    (request.path, besucher_hash),
                )
                conn.commit()
                cur.close()
                conn.close()
            except Exception:
                pass  # Tracking darf nie eine echte Seite kaputt machen
    return response
