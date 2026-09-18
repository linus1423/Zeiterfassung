# Zeiterfassung

Ein Django-Werkzeug zur Arbeitszeiterfassung: Start, Stop und Pausen stempeln,
Nutzer in Gruppen, je Gruppe eigene Taetigkeiten und eigene Admins, Korrektur
der erfassten Zeiten nur ueber Antrag, Anmeldung ueber OpenID Connect gegen
Keycloak oder Microsoft Entra ID.

Die vollstaendige fachliche Beschreibung steht in
[docs/spezifikation.md](docs/spezifikation.md).

## Rollen

| Rolle | Darf |
|---|---|
| Mitarbeitende | eigene Zeit stempeln, eigene Zeiten sehen, Korrektur beantragen |
| Gruppen-Admin | zusaetzlich Taetigkeiten und Mitglieder der eigenen Gruppe pflegen, Zeiten der Gruppe sehen, Korrekturantraege entscheiden |
| Buchhaltung | alle Gruppen lesen und nach Excel oder CSV exportieren, mit frei waehlbaren Spalten; aendert nichts |
| System-Admin | Gruppen anlegen, Rollen vergeben, Django-Adminoberflaeche |

## Schnellstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # Werte eintragen
python manage.py migrate
python manage.py createsuperuser
python manage.py demo_daten     # optional: Beispielgruppe und Taetigkeiten
python manage.py runserver
```

Ohne konfigurierten Identity-Provider gibt es auf der Anmeldeseite keinen Knopf.
Der System-Admin meldet sich dann unter `/admin/` mit Passwort an, legt Gruppen
und Mitglieder an und richtet die Anmeldung ein.

## Anmeldung einrichten

Beide Provider sprechen OpenID Connect und laufen deshalb ueber dieselbe
Mechanik, nur mit anderer Konfiguration. Es kann einer oder es koennen beide
gleichzeitig aktiv sein; pro konfiguriertem Provider erscheint ein Knopf auf der
Anmeldeseite.

**Keycloak**

```
KEYCLOAK_CLIENT_ID=zeiterfassung
KEYCLOAK_CLIENT_SECRET=...
KEYCLOAK_SERVER_URL=https://keycloak.example.com/realms/mein-realm
```

Redirect-URI im Keycloak-Client eintragen:
`https://<host>/accounts/oidc/keycloak/login/callback/`

**Microsoft Entra ID**

```
ENTRA_CLIENT_ID=...
ENTRA_CLIENT_SECRET=...
ENTRA_TENANT_ID=<Verzeichnis-ID>
```

Redirect-URI in der App-Registrierung eintragen:
`https://<host>/accounts/oidc/entra/login/callback/`

Konten entstehen beim ersten Login. Meldet sich derselbe Mensch einmal ueber
Keycloak und einmal ueber Entra an, werden die Konten ueber die E-Mail-Adresse
zusammengefuehrt (`OIDC_LINK_BY_EMAIL`). Das setzt voraus, dass beide Provider
verifizierte Adressen liefern.

## Betrieb

```bash
docker compose up --build
```

Die Datei `.env` wird dabei gelesen; `POSTGRES_PASSWORD` und
`DJANGO_SECRET_KEY` muessen gesetzt sein. Hinter einem Reverse Proxy gehoert
`DJANGO_BEHIND_PROXY=true` in die Umgebung, damit Django die Verbindung als
HTTPS erkennt.

Vergessene Stempelungen werden nicht von selbst beendet. Dafuer laeuft einmal
pro Stunde:

```bash
python manage.py close_stale_entries
```

Der Eintrag wird dann auf die Hoechstdauer (`MAX_OPEN_ENTRY_HOURS`, Vorgabe 16
Stunden) gekuerzt und als unvollstaendig markiert. Die betroffene Person sieht
den Hinweis auf der Stempeluhr und kann eine Korrektur beantragen.

## Entwicklung

```bash
pytest                 # Tests
ruff check .           # Linting
ruff format .          # Formatierung
python manage.py makemigrations --check --dry-run
```

Dieselben Schritte laufen in GitHub Actions bei jedem Push
(`.github/workflows/ci.yml`).

## Aufbau

```
zeiterfassung/      Einstellungen, URLs, WSGI
apps/accounts/      Nutzermodell, OIDC-Anbindung
apps/groups/        Gruppen, Mitgliedschaften, Taetigkeiten, Rechtepruefung
apps/tracking/      Zeiteintraege, Pausen, Stempel-Logik
apps/corrections/   Korrekturantraege und deren Ablauf
apps/reporting/     Auswertung, Spaltenauswahl, Export nach Excel und CSV
apps/audit/         unveraenderliches Protokoll aller Aenderungen
templates/          Oberflaeche (Django-Templates)
```

Die Regeln stehen in `services.py` der jeweiligen App, nicht in den Views.
Rechtepruefungen laufen zentral ueber `apps/groups/permissions.py`.
