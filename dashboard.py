"""Einstiegspunkt (Gunicorn: dashboard:app, siehe Procfile). Die Seiten liegen in den
seiten_*-Modulen - erst ihr Import registriert die Routen an der gemeinsamen App."""
import os

import seiten_allgemein  # noqa: F401
import seiten_champions  # noqa: F401
import seiten_gruppen  # noqa: F401
import seiten_konto  # noqa: F401
import seiten_match  # noqa: F401
import seiten_profil  # noqa: F401
from app_core import ANALYTICS_SECRET, app  # noqa: F401 (ANALYTICS_SECRET für Skripte/Tests)


if __name__ == "__main__":
    # Nur für die lokale Entwicklung - im Live-Betrieb läuft die App über Gunicorn
    # (siehe Procfile), das diesen Block nie ausführt. FLASK_DEBUG=0 als zusätzliche
    # Absicherung, falls die App doch mal versehentlich direkt gestartet wird - im Netz
    # darf debug NIE an sein (offener Remote-Debugger).
    # Port 5000 kollidiert auf macOS oft mit dem AirPlay-Receiver-Dienst.
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, port=port)
