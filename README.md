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

### Gruppen aus dem Identity-Provider

Vorgabe ist, dass die Mitgliedschaften im Tool gepflegt werden. Wer sie statt
dessen aus dem Token uebernehmen will, schaltet das ein:

```
OIDC_GROUP_SYNC=true
OIDC_GROUPS_CLAIM=groups        # Name des Claims
OIDC_GROUP_SYNC_MODE=add        # add ergaenzt, replace entzieht auch wieder
OIDC_ADMIN_GROUPS_CLAIM=        # eigener Claim fuer die Admin-Rolle
OIDC_ADMIN_GROUP_SUFFIX=        # oder Namenskonvention, z. B. -admins
```

Verglichen wird gegen Kurzname, Name und das Feld "Bezeichnung beim
Identity-Provider" der Gruppe; bei verschachtelten Keycloak-Gruppen
(`/werk/werkstatt`) passt auch das letzte Pfadstueck. Im Tool gepflegte
Mitgliedschaften bleiben unangetastet: der Provider legt eigene an und aendert
auch nur diese. Der letzte Admin einer Gruppe verliert die Rolle nie
automatisch. Die Admin-Rolle kommt nur aus dem Token, wenn einer der beiden
letzten Werte gesetzt ist.

## Abrechnungszeitraum und Abschluss

Jede Gruppe rechnet nach Kalendermonat ab oder nach einem eigenen Zyklus, der
am X. eines Monats beginnt (Gruppe > Einstellungen, hoechstens der 28.). Der
Zyklus gilt fuer die Gruppenansicht, die Verdichtung "Je Nutzer und Monat" und
den Abschluss.

Ein Admin der Gruppe schliesst einen abgelaufenen Zeitraum unter
Gruppe > Abrechnungszeitraeume ab. Danach lehnt das System Korrekturantraege
fuer diesen Zeitraum ab, auch bereits gestellte werden nicht mehr genehmigt.
Ein exportierter Monat bleibt so, wie er exportiert wurde. Wieder oeffnen kann
den Abschluss nur ein System-Admin.

## Benachrichtigungen

Ohne Mailserver zeigt die Navigation Zaehler: offene Antraege fuer Admins,
neu entschiedene eigene Antraege fuer Antragstellende. Mit Mailserver kommt
eine Mail an die Admins bei einem neuen Antrag und an den Antragsteller bei der
Entscheidung:

```
CORRECTION_EMAILS_ENABLED=true
SITE_BASE_URL=https://zeiterfassung.example.com
DJANGO_EMAIL_HOST=smtp.example.com
DJANGO_EMAIL_HOST_USER=...
DJANGO_EMAIL_HOST_PASSWORD=...
```

Versandt wird nach dem Commit, im Request und begrenzt durch
`DJANGO_EMAIL_TIMEOUT`. Ein nicht erreichbarer Mailserver verhindert den
Vorgang nicht, der Fehlversuch landet im Protokoll. Bei hohem Mailaufkommen
gehoert an diese Stelle eine Warteschlange.

## Betrieb

```bash
docker compose up --build
```

Die Datei `.env` wird dabei gelesen; `POSTGRES_PASSWORD` und
`DJANGO_SECRET_KEY` muessen gesetzt sein. Hinter einem Reverse Proxy gehoert
`DJANGO_BEHIND_PROXY=true` in die Umgebung, damit Django die Verbindung als
HTTPS erkennt.

Vergessene Stempelungen beendet der Dienst `scheduler` aus dem Compose-Setup.
Er ruft `python manage.py close_stale_entries` in einer Schleife auf, standard-
maessig jede Stunde (`SCHEDULER_INTERVAL_SECONDS`). Wer ohne Compose betreibt,
legt dafuer einen Cronjob oder einen systemd-Timer an:

```bash
python manage.py close_stale_entries
```

Der Eintrag wird dann auf die Hoechstdauer (`MAX_OPEN_ENTRY_HOURS`, Vorgabe 16
Stunden) gekuerzt und als unvollstaendig markiert. Die betroffene Person sieht
den Hinweis auf der Stempeluhr und kann eine Korrektur beantragen.

### Aufbewahrung

Arbeitszeitdaten sind personenbezogen und werden nicht unbegrenzt aufbewahrt.
Nach `DATA_RETENTION_MONTHS` (Vorgabe 24 Monate) ohne Zeiten, ohne Anmeldung
und ohne offenen Antrag wird ein Konto anonymisiert: Name, Adresse und
Personalnummer verschwinden, das Konto wird deaktiviert, die Zeiteintraege
bleiben fuer die Statistik erhalten, sind aber keiner Person mehr zuzuordnen.

```bash
python manage.py anonymize_expired_users            # zeigt nur an
python manage.py anonymize_expired_users --apply    # anonymisiert
```

Das Kommando laeuft nicht von selbst: Loeschen ist nicht umkehrbar, deshalb
entscheidet der Betrieb, wann es laeuft.

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
apps/groups/        Gruppen, Mitgliedschaften, Taetigkeiten, Zeitraeume, Rechtepruefung
apps/tracking/      Zeiteintraege, Pausen, Stempel-Logik
apps/corrections/   Korrekturantraege und deren Ablauf
apps/reporting/     Auswertung, Spaltenauswahl, Export nach Excel und CSV
apps/audit/         unveraenderliches Protokoll aller Aenderungen
templates/          Oberflaeche (Django-Templates)
```

Die Regeln stehen in `services.py` der jeweiligen App, nicht in den Views.
Rechtepruefungen laufen zentral ueber `apps/groups/permissions.py`.
