# Zeiterfassung

Ein Django-Werkzeug zur Arbeitszeiterfassung: Start, Stop und Pausen stempeln,
Nutzer in Gruppen, je Gruppe eigene Tätigkeiten und eigene Admins, Korrektur
der erfassten Zeiten nur über Antrag, Anmeldung über OpenID Connect gegen
Keycloak oder Microsoft Entra ID.

Die vollständige fachliche Beschreibung steht in
[docs/spezifikation.md](docs/spezifikation.md).

## Rollen

| Rolle | Darf |
|---|---|
| Mitarbeitende | eigene Zeit stempeln, eigene Zeiten sehen, Korrektur beantragen |
| Gruppen-Admin | zusätzlich Tätigkeiten und Mitglieder der eigenen Gruppe pflegen, Zeiten der Gruppe sehen, Korrekturanträge entscheiden |
| Buchhaltung | alle Gruppen lesen und nach Excel oder CSV exportieren, mit frei wählbaren Spalten; ändert nichts |
| System-Admin | Gruppen anlegen, Rollen vergeben, Django-Adminoberfläche |

## Schnellstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # Werte eintragen
python manage.py migrate
python manage.py createsuperuser
python manage.py demo_daten     # optional: Beispielgruppe und Tätigkeiten
python manage.py runserver
```

Ohne konfigurierten Identity-Provider gibt es auf der Anmeldeseite keinen Knopf.
Der System-Admin meldet sich dann unter `/admin/` mit Passwort an und richtet die
Anmeldung ein. Gruppen legt er danach unter Gruppen > Neue Gruppe an, Mitglieder
kommen dazu, sobald sie sich einmal angemeldet haben.

## Anmeldung einrichten

Beide Provider sprechen OpenID Connect und laufen deshalb über dieselbe
Mechanik, nur mit anderer Konfiguration. Es kann einer oder es können beide
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

Konten entstehen beim ersten Login. Meldet sich derselbe Mensch einmal über
Keycloak und einmal über Entra an, werden die Konten über die E-Mail-Adresse
zusammengeführt (`OIDC_LINK_BY_EMAIL`). Das setzt voraus, dass beide Provider
verifizierte Adressen liefern.

### Gruppen aus dem Identity-Provider

Vorgabe ist, dass die Mitgliedschaften im Tool gepflegt werden. Wer sie statt
dessen aus dem Token übernehmen will, schaltet das ein:

```
OIDC_GROUP_SYNC=true
OIDC_GROUPS_CLAIM=groups        # Name des Claims
OIDC_GROUP_SYNC_MODE=add        # add ergänzt, replace entzieht auch wieder
OIDC_ADMIN_GROUPS_CLAIM=        # eigener Claim für die Admin-Rolle
OIDC_ADMIN_GROUP_SUFFIX=        # oder Namenskonvention, z. B. -admins
```

Verglichen wird gegen Kurzname, Name und das Feld "Bezeichnung beim
Identity-Provider" der Gruppe; bei verschachtelten Keycloak-Gruppen
(`/werk/werkstatt`) passt auch das letzte Pfadstück. Im Tool gepflegte
Mitgliedschaften bleiben unangetastet: der Provider legt eigene an und ändert
auch nur diese. Der letzte Admin einer Gruppe verliert die Rolle nie
automatisch. Die Admin-Rolle kommt nur aus dem Token, wenn einer der beiden
letzten Werte gesetzt ist.

## Abrechnungszeitraum und Abschluss

Jede Gruppe rechnet nach Kalendermonat ab oder nach einem eigenen Zyklus, der
am X. eines Monats beginnt (Gruppe > Einstellungen, höchstens der 28.). Der
Zyklus gilt für die Gruppenansicht, die Verdichtung "Je Nutzer und Monat" und
den Abschluss.

Ein Admin der Gruppe schließt einen abgelaufenen Zeitraum unter
Gruppe > Abrechnungszeiträume ab. Danach lehnt das System Korrekturanträge
für diesen Zeitraum ab, auch bereits gestellte werden nicht mehr genehmigt.
Ein exportierter Monat bleibt so, wie er exportiert wurde. Wieder öffnen kann
den Abschluss nur ein System-Admin.

Wo der Abschluss insgesamt steht, zeigt Gruppen > Abschlüsse aller Gruppen: je
Gruppe der letzte Abschluss, der älteste abgelaufene Zeitraum ohne Abschluss
und die Zahl der offenen Korrekturanträge und unvollständigen Einträge in genau
diesem Zeitraum. Das ist die Liste für den Monatslauf. Gruppen-Admins sehen dort
ihre Gruppen, die Buchhaltung und System-Admins alle; verlinkt ist die Seite
auch aus der Auswertung. Abschließen darf weiterhin nur ein Admin der Gruppe.

## Arbeitszeitnachweis

Zum Unterschreiben gibt es ein Blatt je Person und Zeitraum: eine Zeile pro
Tag mit Datum, Wochentag, Beginn, Ende, Pause und Arbeitszeit, darunter die
Summe und Platz für Datum und Unterschrift. Tage ohne Erfassung bleiben leer,
damit Lücken auffallen. Im Kopf stehen Name, Personalnummer, Gruppe und ob der
Zeitraum schon abgeschlossen ist, damit ein unterschriebenes Blatt später nicht
mehr abweicht.

Mitarbeitende finden den Nachweis unter "Meine Zeiten", Gruppen-Admins unter
Gruppe > Nachweise für alle Mitglieder auf einmal; die Buchhaltung sieht ihn
für jede Gruppe. Gedruckt wird aus dem Browser: ein eigenes Druck-Stylesheet
blendet Navigation und Knöpfe aus und beginnt für jede Person eine neue Seite.
Eine PDF-Ausgabe gibt es bewusst nicht, das spart eine Abhängigkeit.

Laufende Einträge zählen auf dem Blatt nicht mit und werden darunter vermerkt;
automatisch beendete Tage sind als unvollständig gekennzeichnet.

## Benachrichtigungen

Ohne Mailserver zeigt die Navigation Zähler: offene Anträge für Admins,
neu entschiedene eigene Anträge für Antragstellende. Mit Mailserver kommt
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
gehört an diese Stelle eine Warteschlange.

### Erinnerungen

Benachrichtigt wird damit nur nachträglich. Daneben gibt es drei Hinweise,
die Arbeit ersparen, bevor etwas schiefgeht:

| Hinweis | Anlass | Empfänger |
| --- | --- | --- |
| Ausstempeln vergessen | länger als `OPEN_ENTRY_REMINDER_HOURS` eingestempelt | die Person selbst |
| Antrag liegt offen | Antrag älter als `PENDING_CORRECTION_REMINDER_DAYS` | Admins der Gruppe |
| Zeitraum noch offen | Zeitraum seit `PERIOD_CLOSING_REMINDER_DAYS` abgelaufen | Admins der Gruppe |

Sie stehen im Tool unter **Hinweise**, mit einem Zähler in der Navigation.
Eine Mail dazu gibt es nur mit `REMINDER_EMAILS_ENABLED=true`. Jeder Hinweis
entsteht genau einmal je Anlass, nicht bei jedem Lauf des Diensts, und
verschwindet wieder, sobald der Anlass erledigt ist: nach dem Ausstempeln,
nach der Entscheidung, nach dem Abschluss. Wer einen Hinweis ausblendet,
bekommt ihn zu diesem Anlass nicht noch einmal.

Die Schwelle fürs Ausstempeln gehört deutlich unter `MAX_OPEN_ENTRY_HOURS`,
sonst kommt der Hinweis erst, wenn `close_stale_entries` den Eintrag schon
gekappt hat.

## Betrieb

### Docker Compose

```bash
docker compose up --build
```

Die Datei `.env` wird dabei gelesen; `POSTGRES_PASSWORD` und
`DJANGO_SECRET_KEY` müssen gesetzt sein. Hinter einem Reverse Proxy gehört
`DJANGO_BEHIND_PROXY=true` in die Umgebung, damit Django die Verbindung als
HTTPS erkennt.

Die Migrationen legen unter PostgreSQL die Erweiterung `btree_gist` an; das
Datenbankkonto muss das dürfen (Eigentümer der Datenbank oder Superuser). Sie
trägt die Bedingung, dass sich die Zeiten einer Person nicht überschneiden
dürfen.

### Podman mit Quadlet

Derselbe Aufbau läuft rootless unter systemd. Die Unit-Dateien liegen in
`deploy/quadlet/`, die Anleitung in
[docs/podman-quadlet.md](docs/podman-quadlet.md). Beide Wege benutzen dasselbe
Image und denselben Einstiegspunkt.

### Zustand prüfen

| Pfad | Bedeutung |
|---|---|
| `/healthz` | der Prozess antwortet; das fragt der Healthcheck des Containers |
| `/readyz` | zusätzlich: die Datenbank ist erreichbar; das fragt ein Loadbalancer |

Beide Pfade brauchen keine Anmeldung und antworten als reiner Text. Wer sie
nicht von außen erreichbar haben will, blockt sie im Reverse Proxy.

### Geheimnisse als Datei

`DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_EMAIL_HOST_PASSWORD`,
`KEYCLOAK_CLIENT_SECRET` und `ENTRA_CLIENT_SECRET` dürfen statt als
Umgebungsvariable auch als Datei übergeben werden, indem `FOO_FILE` auf den
Pfad zeigt:

```
DJANGO_SECRET_KEY_FILE=/run/secrets/django-secret-key
```

Damit funktionieren `podman secret` und `docker secret`. Eine gesetzte
Umgebungsvariable hat Vorrang.

### Wiederkehrende Aufgaben

Unter Compose erledigt sie der Dienst `scheduler`. Er ruft die folgenden
Kommandos in einer Schleife auf, standardmäßig jede Stunde
(`SCHEDULER_INTERVAL_SECONDS`). Unter Podman macht das ein systemd-Timer.
Wer ohne beides betreibt, legt dafür einen Cronjob oder einen systemd-Timer
an:

```bash
python manage.py close_stale_entries          # vergessene Stempelungen beenden
python manage.py remind_open_entries          # ans Ausstempeln erinnern
python manage.py remind_pending_corrections   # an offene Anträge erinnern
python manage.py remind_period_closing        # an den Abschluss erinnern
```

Jedes Kommando ist unschädlich, wenn es nichts zu tun gibt.

Der Eintrag wird dann auf die Höchstdauer (`MAX_OPEN_ENTRY_HOURS`, Vorgabe 16
Stunden) gekürzt und als unvollständig markiert. Die betroffene Person sieht
den Hinweis auf der Stempeluhr und kann eine Korrektur beantragen.

### Zeiten aus CSV importieren

Bei der Einführung kommt fast immer eine Liste aus dem Altsystem mit. Ein
System-Admin lädt sie unter „Import“ hoch; dort gibt es auch eine
Beispieldatei mit Kopfzeile. Das Hochladen prüft nur und zeigt Zeilenzahl,
erkannte Nutzer und Gruppen sowie jeden Fehler mit Zeilennummer. Geschrieben
wird erst im zweiten Schritt, und nur dann, wenn keine einzige Zeile
fehlerhaft ist. Alles wird als `source=import` angelegt und steht im
Protokoll.

Dasselbe von der Kommandozeile aus:

```bash
python manage.py import_time_entries zeiten.csv                 # prüft nur
python manage.py import_time_entries zeiten.csv --uebernehmen \
    --akteur system@example.com                                 # schreibt
```

Erwartet wird deutsches CSV (Semikolon, UTF-8). Spalten: `Personalnummer`
oder `E-Mail`, `Gruppe`, `Tätigkeit`, `Datum`, `Beginn`, `Ende`, `Pausen`,
`Notiz`; Pflicht sind `Gruppe`, `Beginn`, `Ende` und eine Spalte zur Person.
Beginn und Ende dürfen `TT.MM.JJJJ HH:MM` heißen oder nur `HH:MM`, wenn es
eine Spalte `Datum` gibt; ein Ende vor dem Beginn zählt dann als Folgetag.
`Pausen` ist entweder eine Dauer (`30` oder `0:30`, sie wird mittig in die
Arbeitszeit gelegt) oder ein Zeitraum wie `11:30-12:00`, mehrere durch Komma
getrennt.

### Aufbewahrung

Arbeitszeitdaten sind personenbezogen und werden nicht unbegrenzt aufbewahrt.
Nach `DATA_RETENTION_MONTHS` (Vorgabe 24 Monate) ohne Zeiten, ohne Anmeldung
und ohne offenen Antrag wird ein Konto anonymisiert: Name, Adresse und
Personalnummer verschwinden, das Konto wird deaktiviert, die Zeiteinträge
bleiben für die Statistik erhalten, sind aber keiner Person mehr zuzuordnen.

```bash
python manage.py anonymize_expired_users            # zeigt nur an
python manage.py anonymize_expired_users --apply    # anonymisiert
```

Das Kommando läuft nicht von selbst: Löschen ist nicht umkehrbar, deshalb
entscheidet der Betrieb, wann es läuft.

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
zeiterfassung/      Einstellungen, URLs, WSGI, Health-Endpunkte
apps/accounts/      Nutzermodell, OIDC-Anbindung
apps/groups/        Gruppen, Mitgliedschaften, Tätigkeiten, Zeiträume, Rechteprüfung
apps/tracking/      Zeiteinträge, Pausen, Stempel-Logik, CSV-Import
apps/corrections/   Korrekturanträge und deren Ablauf
apps/reporting/     Auswertung, Spaltenauswahl, Export nach Excel und CSV
apps/audit/         unveränderliches Protokoll aller Änderungen
templates/          Oberfläche (Django-Templates)
docker/             Einstiegspunkt und Healthcheck des Containers
deploy/quadlet/     systemd-Units für den Betrieb mit Podman
```

Die Regeln stehen in `services.py` der jeweiligen App, nicht in den Views.
Rechteprüfungen laufen zentral über `apps/groups/permissions.py`.
