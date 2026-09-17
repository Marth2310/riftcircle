import os

import psycopg2


def get_connection():
    """Verbindet mit Postgres. Nutzt DATABASE_URL (Standard bei den meisten Hosting-
    Plattformen wie Render/Railway), falls gesetzt - sonst die lokale Standard-Verbindung
    für die Entwicklung (aus DB_PASSWORD in der .env)."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)

    return psycopg2.connect(
        host="localhost", port=5432, dbname="lolanalytics",
        user="postgres", password=os.environ["DB_PASSWORD"]
    )
