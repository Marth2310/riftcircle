# RiftCircle

League-of-Legends-Statistik-Seite. Flask + Jinja2 + Postgres, deployed auf Railway unter
https://riftcircle.com. Alle Bezeichner (Variablen, Funktionen, Kommentare, Commit-Texte)
sind auf Deutsch, außer wo Riot/Discord-API-Feldnamen übernommen werden.

## Architektur

`dashboard.py` ist nur der Entry-Point (`gunicorn dashboard:app`). Er importiert
`seiten_*.py`-Module, die Routen direkt auf der gemeinsamen `app` aus `app_core.py`
registrieren (keine Blueprints — Endpunktnamen bleiben dadurch stabil):

- `seiten_allgemein.py` — Landing, Favicon, `/riot.txt`, Namenssuche-API, `/stats/<key>`
- `seiten_champions.py` — Champion-Datenbank (`/champions?rolle=`) und Champion-Detail
- `seiten_gruppen.py` — Gruppen: Feed, Rangliste, Verlauf, Discord-Webhook-Posts
- `seiten_konto.py` — Discord-Login/Logout, Konto-Einstellungen, Riot-Verknüpfung
- `seiten_match.py` — Match-Detailseite (4 Tabs: Übersicht/Team/Karte/Tipps)
- `seiten_profil.py` — Spielerprofil (Match-History, Rang-Verlauf, Meistgespielte)

Sonstige Kernmodule:
- `app_core.py` — Flask-App, Env-Vars, Session-Config, Context-Processor
- `web_hilfen.py` — geteilte Helfer (zuletzt gesehen, meine Gruppen, Namenssuche, Login-Session)
- `db.py` — `get_connection()`
- `riot_fetch.py` — Riot-API-Calls, `sync_player()` (Kern-Sync-Logik), Fehlerklassen
- `riot_assets.py` — Data-Dragon-Cache (Champion-JSON, Skins, Spell-Icons)
- `riot_runes.py`, `riot_timeline.py` — Runen- bzw. Timeline-spezifische Riot-Aufrufe
- `analysis.py` — `MATCH_QUERY`, `match_metrics()` (Kennzahlen + Elo-Vergleich pro Match)
- `champion_stats.py` — Champion-DB-Statistiken (Runen-Varianten, Skill-Pfad, Item-Infos)
- `profil_statistik.py` — Rang-Historie, Rollen-Statistik, Duo-Partner
- `match_tipps.py` — individualisierte Tipps auf der Match-Detailseite
- `spielmodus.py` — queueId → (Anzeigename, Kategorie) für Ranked/Normal/ARAM/Sonstige
- `achievements.py`, `wochenrueckblick.py` — Gruppen-Achievements bzw. Wochenrückblick-Score
- `discord_webhook.py`, `discord_login.py` — Discord-Integration
- `create_tables.py` — Schema (idempotent: `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`)
- `harvest_meta.py` — holt Challenger-Matches für die Champion-Datenbank (Meta-Stichproben)
- `backfill_*.py` — einmalige/wiederholbare Nachlade-Skripte für neu eingeführte Spalten

Templates unter `templates/`, geteilte Partials mit `_`-Präfix (`_runen.html`,
`_skill_raster.html`, `_konto_ecke.html`). Gemeinsames CSS in `static/style.css` — Filter-
Chips, Modus-Badges, Konto-Ecke, Rune-Mini-Anzeige usw. liegen dort zentral, nicht in
einzelnen Templates.

## Datenbank

Kern-Tabellen: `players`, `matches`, `participants`, `gruppen`, `gruppen_mitglieder`,
`nutzer` (Discord-Konten), `nutzer_gruppen`, `nutzer_zuletzt_gesehen`, `rang_verlauf`,
`page_views`, `bekannte_spieler` (Namensindex für die Suche ohne Tag), `discord_posts`
(Claim-before-send gegen Doppel-Posts).

Schema-Änderungen: in `create_tables.py` ergänzen (immer `IF NOT EXISTS`), dann lokal
`python create_tables.py`, nach dem Deploy `railway ssh --service web -- python create_tables.py`.

## Deploy-Workflow

```
git push
railway up --service web -y --detach --json          # gibt deploymentId zurück
railway deployment list --service web --json          # Feld heißt "status", NICHT "st"
railway ssh --service web -- python create_tables.py   # nur bei Schema-Änderungen
```

- `railway variables --set KEY=VALUE` deployt NICHT automatisch neu — danach `railway up`.
- Secrets (`RIOT_API_KEY`, `DISCORD_CLIENT_SECRET`, `FLASK_SECRET_KEY`, `ANALYTICS_SECRET`)
  nie ausgeben/committen. Aus `.env` lesen und per subprocess setzen, ohne sie zu printen.
- `.env` ist gitignored.

## Riot-API-Key

Aktuell ein **persönlicher Dev-Key**, läuft alle 24h ab. Der Nutzer erneuert ihn täglich
selbst und trägt ihn in `.env` ein; ich übernehme ihn dann unverändert nach Railway und
triggere `railway up`. Details zum Wartestatus (was auf den Production Key wartet) stehen
in meinem Memory (`project_riot_production_key.md`), nicht hier — das ändert sich mit der
Zeit und gehört nicht ins Repo.

Lange/massenhafte Riot-API-Jobs (Backfills über tausende Matches, automatisches Harvesting)
NICHT gegen Prod laufen lassen, solange nur der Dev-Key existiert — der teilt sich das
Rate-Limit mit der Live-Seite und läuft nach spätestens 24h ins Leere.

## Konventionen

- Deutsche Namen durchgängig: `spieler`, `gruppe`, `zuletzt_gesehen`, `rang_verlauf` usw.
- Flask-Session: signierter Cookie, 30 Tage, `SameSite=Lax`, `Secure` in Prod.
- Per-Request-Cache über `flask.g` (z.B. `lese_zuletzt_gesehen`).
- Open-Redirect-Schutz: `_sicheres_ziel()` in `web_hilfen.py` — bei jedem neuen
  benutzergesteuerten Redirect-Ziel verwenden.
- Filter-Chips (aktiv/inaktiv, `data-*`-Attribute + kleines JS) sind das Standard-Muster
  für Listen-Filter (Gruppen-Feed nach Mitglied/Rolle, Match-History nach Spielmodus) —
  gemeinsames CSS in `static/style.css`, kein neues Filter-UI-Pattern erfinden.
- Screenshot-Tests: Firefox headless mit eigenem Profil
  (`--no-remote --profile scratchpad/ffprof`), Animationen/Lazy-Loading in Testkopien
  deaktivieren, damit Screenshots deterministisch sind.
