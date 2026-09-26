# Betrieb mit Podman und Quadlet

Der Standardbetrieb der Zeiterfassung ist rootless Podman unter systemd, mit
Quadlet-Units. Docker Compose (`docker compose up --build`) bleibt als
Alternative, etwa für die Entwicklung. Beide Wege benutzen dasselbe Image und
denselben Einstiegspunkt, es gibt also keine zweite Variante des Codes.

Die Unit-Dateien liegen in [`deploy/quadlet/`](../deploy/quadlet).

## Installation mit install-dnf.sh

Auf Fedora, AlmaLinux, Rocky und RHEL macht `install-dnf.sh` im Wurzelverzeichnis
des Repositorys alle Schritte dieser Anleitung in einem Durchgang:

```bash
git clone https://github.com/linus1423/Zeiterfassung.git
cd Zeiterfassung
sudo ./install-dnf.sh
```

Das Skript fragt zuerst alles ab, zeigt eine Zusammenfassung und legt erst
nach der Bestätigung los:

| Frage | Vorgabe |
|---|---|
| Dienstbenutzer | `zeiterfassung` |
| Verzeichnis für die Quellen | `~/zeiterfassung` des Dienstbenutzers |
| Verzeichnis für die Sicherungen | `~/zeiterfassung-sicherungen` |
| Verzeichnis für die Datenbank | leer, also das Volume `zeiterfassung-pgdata` |
| lokaler Port | `8000`, nur an `127.0.0.1` |
| öffentlicher Hostname | `hostname -f` |
| Keycloak, Entra ID | Client-ID, Realm-URL bzw. Tenant-ID; leer heißt aus |
| Mailversand | aus; sonst Server, Port, Benutzer, Absender |
| nginx mit HTTPS | ja; Zertifikat und Schlüssel, leer erzeugt ein selbstsigniertes |

Danach installiert es mit `dnf` Podman (mindestens 4.7), `openssl`, `curl`
und auf Wunsch nginx, legt den Dienstbenutzer mit Subuid-Bereich und
Lingering an, kopiert die Quellen in dessen Verzeichnis und baut dort das
Image. Das Datenbankpasswort und `DJANGO_SECRET_KEY` erzeugt es selbst, die
Client-Secrets von Keycloak und Entra ID und das Mailpasswort fragt es verdeckt
ab. Alle landen als `podman secret` beim Dienstbenutzer und werden als Datei in
die Container gehängt (siehe unten); das Skript hängt die nötigen
`Secret=`-Zeilen selbst an die Units. Zum Schluss startet es Datenbank,
Webdienst und die beiden Timer, wartet auf `/readyz`, richtet nginx mit
SELinux-Schalter und Firewall ein und bietet an, gleich ein System-Admin-Konto
anzulegen.

**Erneut aufrufen spielt eine neue Version ein.** Nach `git pull` einfach
`sudo ./install-dnf.sh` wiederholen: die Antworten des letzten Laufs stehen in
`/etc/zeiterfassung/install.conf` und werden als Vorgabe angeboten, eigene
Zeilen in der `zeiterfassung.env` bleiben stehen (die alte Fassung liegt als
`.bak` daneben), vorhandene Geheimnisse ebenso. Ein leeres Client-Secret heißt
„bisheriges behalten“. Das Datenbankpasswort ersetzt das Skript nie, weil es
nicht mehr zur bestehenden Datenbank passen würde.

**Ohne Rückfragen**, etwa aus Ansible: `--ja`. Die Vorgaben lassen sich dann
über Umgebungsvariablen setzen, die Liste zeigt `./install-dnf.sh --hilfe`:

```bash
sudo ZE_DOMAIN=zeit.example.org \
     KEYCLOAK_CLIENT_ID=zeiterfassung \
     KEYCLOAK_SERVER_URL=https://keycloak.example.org/realms/verein \
     KEYCLOAK_CLIENT_SECRET="$(cat keycloak-secret)" \
     ./install-dnf.sh --ja
```

Das Quellverzeichnis wird bei jedem Lauf geleert und neu befüllt. Deshalb
lehnt das Skript ein Verzeichnis ab, das schon etwas anderes enthält, und
eines, in dem Home, Sicherungen oder Datenbank liegen.

Die Abschnitte unten beschreiben dieselben Schritte von Hand, für andere
Distributionen und für alle, die wissen wollen, was das Skript tut.

## Was Quadlet anders macht als Compose

| Compose | Quadlet |
|---|---|
| `depends_on: service_healthy` | gibt es nicht; der Einstiegspunkt im Container wartet selbst auf die Datenbank |
| Dienst `scheduler` mit `sleep`-Schleife | systemd-Timer, zwischen zwei Läufen läuft kein Container |
| `${VAR}` aus der `.env` wird eingesetzt | keine Ersetzung; zusammengesetzte Werte wie `DATABASE_URL` werden fertig hinterlegt |
| `restart: unless-stopped` | `Restart=always` in der Unit, Start beim Login über `[Install]` |
| Passwörter als Umgebungsvariable | `podman secret`, als Datei in den Container gehängt |
| läuft als root (Docker-Daemon) | läuft rootless als gewöhnlicher Benutzer |

## Voraussetzungen

* Podman 4.7 oder neuer (Quadlet mit `Secret=`), getestet gegen die 5er-Reihe.
* Ein eigener Benutzer für den Dienst, mit Subuid- und Subgid-Bereich
  (`/etc/subuid`, `/etc/subgid`; bei den üblichen Distributionen legt
  `useradd` das selbst an).
* Eingeschaltetes Lingering, sonst beendet systemd die Dienste, sobald sich der
  Benutzer abmeldet:

  ```bash
  sudo loginctl enable-linger zeiterfassung
  ```

Alle folgenden Befehle laufen als dieser Benutzer, ohne `sudo`.

## Einrichten

### 1. Image bauen

```bash
git clone https://github.com/linus1423/Zeiterfassung.git
cd Zeiterfassung
podman build -t localhost/zeiterfassung:latest .
```

### 2. Geheimnisse anlegen

```bash
# Nur Buchstaben und Ziffern: das Passwort steht gleich in einer URL, und
# "openssl rand -base64" liefert auch "/" und "+", die dort etwas anderes
# bedeuten würden.
DB_PASSWORT="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40)"

printf '%s' "$DB_PASSWORT" | podman secret create zeiterfassung-db-password -
printf 'postgres://zeiterfassung:%s@zeiterfassung-db:5432/zeiterfassung' "$DB_PASSWORT" \
    | podman secret create zeiterfassung-database-url -
openssl rand -base64 48 | podman secret create zeiterfassung-django-secret-key -
```

`zeiterfassung-db` ist der Containername der Datenbank; die Namensauflösung im
Podman-Netz macht daraus die richtige Adresse. Wer ein bestehendes Passwort
übernimmt, das Sonderzeichen enthält, muss es in der URL prozentkodieren
(`@` als `%40`, `/` als `%2F` und so weiter).

Django liest diese drei Werte aus Dateien, nicht aus der Umgebung
(`DJANGO_SECRET_KEY_FILE`, `DATABASE_URL_FILE`). Der Grund: eine
Umgebungsvariable steht in `podman inspect`, im Journal eines fehlgeschlagenen
Starts und in der Umgebung jedes Kindprozesses. Unterstützt wird das für
`DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_EMAIL_HOST_PASSWORD`,
`KEYCLOAK_CLIENT_SECRET` und `ENTRA_CLIENT_SECRET`; eine gesetzte
Umgebungsvariable hat weiterhin Vorrang, damit der Compose-Weg unverändert
funktioniert.

Für ein weiteres Geheimnis, zum Beispiel das Mailpasswort:

```bash
printf '%s' 'geheim' | podman secret create zeiterfassung-email-password -
```

und in der Unit `zeiterfassung-web.container` ergänzen:

```ini
Environment=DJANGO_EMAIL_HOST_PASSWORD_FILE=/run/secrets/email-password
Secret=zeiterfassung-email-password,type=mount,target=email-password
```

### 3. Konfiguration hinterlegen

```bash
install -Dm600 deploy/quadlet/zeiterfassung.env.example \
    ~/.config/zeiterfassung/zeiterfassung.env
$EDITOR ~/.config/zeiterfassung/zeiterfassung.env
```

Mindestens `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` und
`SITE_BASE_URL` anpassen.

### 4. Units einsetzen

```bash
install -Dm644 -t ~/.config/containers/systemd/ \
    deploy/quadlet/zeiterfassung.network \
    deploy/quadlet/zeiterfassung-pgdata.volume \
    deploy/quadlet/zeiterfassung-db.container \
    deploy/quadlet/zeiterfassung-web.container \
    deploy/quadlet/zeiterfassung-scheduler.container

install -Dm644 -t ~/.config/systemd/user/ \
    deploy/quadlet/zeiterfassung-scheduler.timer

systemctl --user daemon-reload
```

Der Timer gehört nicht in das Quadlet-Verzeichnis: Quadlet erzeugt aus
`zeiterfassung-scheduler.container` die Unit `zeiterfassung-scheduler.service`,
und der Timer ist eine gewöhnliche systemd-Unit, die diesen Dienst startet.

Ob die Units fehlerfrei übersetzt wurden, zeigt:

```bash
/usr/libexec/podman/quadlet -user -dryrun
```

### 5. Starten

```bash
systemctl --user start zeiterfassung-db.service zeiterfassung-web.service
systemctl --user enable --now zeiterfassung-scheduler.timer

systemctl --user status zeiterfassung-web.service
podman ps
```

Die Dienste mit `start`, die Timer mit `enable --now`. Die Dienste erzeugt
Quadlet, und erzeugte Units lehnt `systemctl enable` ab („Unit … is transient
or generated“). Das Einschalten übernimmt dort der Abschnitt `[Install]` mit
`WantedBy=default.target` in der `.container`-Datei: Quadlet legt die
Verknüpfung beim `daemon-reload` selbst an, mit Lingering starten die Dienste
dann auch nach einem Neustart des Servers. Die Timer sind gewöhnliche Units und
brauchen `enable`.

Der erste Start dauert länger: der Webcontainer wartet auf die Datenbank und
wendet die Migrationen an (`journalctl --user -u zeiterfassung-web -f`).

### 6. Ersten Zugang einrichten

```bash
podman exec -it zeiterfassung-web python manage.py createsuperuser
```

Danach wie im [README](../README.md) beschrieben unter `/admin/` anmelden und
die Anmeldung über Keycloak oder Entra ID einrichten.

### 7. Sicherung einrichten

Wie die nächtliche Sicherung eingerichtet wird und wie der Weg zurück
aussieht, steht unten unter [Sicherung und
Rückspielen](#sicherung-und-rückspielen).

## Punkte, die rootless eine Rolle spielen

**Benutzer im Container.** Das Image legt Benutzer und Gruppe `app` mit UID und
GID 10001 an und läuft nicht als root. Rootless bildet Podman diese UID auf den
Subuid-Bereich des Hostbenutzers ab; im Container heißt sie weiterhin 10001,
auf dem Host gehören die Dateien einer hohen, sonst unbenutzten UID. Das ist
gewollt und braucht keine Anpassung, solange keine Hostverzeichnisse eingehängt
werden.

**Volumes.** Die Datenbank liegt in einem benannten Volume
(`zeiterfassung-pgdata.volume`, tatsächlich unter
`~/.local/share/containers/storage/volumes/`). Ein Bind-Mount auf ein
Hostverzeichnis bräuchte rootless erst `podman unshare chown 999:999 <pfad>`,
damit der PostgreSQL-Benutzer im Container schreiben darf, und unter SELinux
zusätzlich die Option `:Z`:

```ini
Volume=/srv/zeiterfassung/pgdata:/var/lib/postgresql/data:Z
```

Ohne `:Z` verbietet SELinux dem Container den Zugriff (`Permission denied` trotz
passender Dateirechte). Bei benannten Volumes setzt Podman das Label selbst.

**Ports.** Rootless dürfen keine Ports unter 1024 belegt werden. Die Unit
veröffentlicht deshalb `127.0.0.1:8000`; davor gehört ein Reverse Proxy, der
HTTPS beendet und auf diesen Port weiterleitet. Mit
`DJANGO_BEHIND_PROXY=true` erkennt Django die Verbindung dann als HTTPS.

**Rechte im Container.** Web und Scheduler laufen mit `ReadOnly=true`,
`NoNewPrivileges=true` und `DropCapability=ALL`. Der Anwendungscode ändert sich
zur Laufzeit nicht, statische Dateien entstehen beim Bauen des Images, und
gunicorn braucht nur ein beschreibbares `/tmp`, das Podman bei `--read-only`
selbst als tmpfs einhängt. Die Datenbank läuft ohne diese Einschränkungen,
weil der PostgreSQL-Einstiegspunkt beim ersten Start das Datenverzeichnis
anlegen und die Rechte setzen muss.

**Healthchecks.** Das Image bringt einen Healthcheck mit, den Docker und Podman
gleichermaßen benutzen. Er fragt `/healthz` an, einen Endpunkt, der nur sagt,
dass der Prozess antwortet. `/readyz` prüft zusätzlich die Datenbank und ist
das, was ein Loadbalancer fragen sollte; als Healthcheck des Containers wäre es
falsch, weil eine kurze Störung der Datenbank sonst den Webdienst neu startet.
Beide Endpunkte werden von einer Middleware beantwortet, bevor Django den Host
prüft, sonst würde eine Anfrage an `127.0.0.1` an `DJANGO_ALLOWED_HOSTS`
scheitern. Der Scheduler-Container hat keinen Webserver und schaltet den
Healthcheck mit `HealthCmd=none` ab.

**Start beim Hochfahren.** Rootless-Dienste starten nicht beim Systemstart,
sondern beim ersten Login des Benutzers, es sei denn, Lingering ist
eingeschaltet (siehe Voraussetzungen).

## Betrieb

```bash
# Protokolle
journalctl --user -u zeiterfassung-web -f
journalctl --user -u zeiterfassung-scheduler --since today

# Nächster Lauf des Timers
systemctl --user list-timers zeiterfassung-scheduler.timer

# Lauf von Hand auslösen
systemctl --user start zeiterfassung-scheduler.service

# Zustand der Healthchecks
podman healthcheck run zeiterfassung-web
podman inspect --format '{{.State.Health.Status}}' zeiterfassung-db
```

**Neue Version einspielen**

```bash
git pull
podman build -t localhost/zeiterfassung:latest .
systemctl --user restart zeiterfassung-web.service
```

Die Migrationen laufen beim Start des Webcontainers.

**Geheimnis wechseln.** Podman-Secrets lassen sich nicht ändern, nur ersetzen:

```bash
systemctl --user stop zeiterfassung-web.service
podman secret rm zeiterfassung-django-secret-key
openssl rand -base64 48 | podman secret create zeiterfassung-django-secret-key -
systemctl --user start zeiterfassung-web.service
```

Ein neuer `DJANGO_SECRET_KEY` macht alle Sitzungen ungültig, alle Nutzer
müssen sich neu anmelden.

## Sicherung und Rückspielen

Arbeitszeitdaten müssen aufbewahrt werden, und eine Sicherung, die noch nie
zurückgespielt wurde, ist keine. Deshalb steht hier der vollständige Weg hin
und zurück.

Beide Richtungen sind Management-Kommandos und laufen für PostgreSQL wie für
SQLite:

```bash
python manage.py backup_database      # sichern
python manage.py restore_database     # zurückspielen, fragt vorher nach
```

`backup_database` schreibt nach `BACKUP_DIR` eine Datei mit Zeitstempel im
Namen (`zeiterfassung-20260921-023000.sql.gz` unter PostgreSQL,
`…-023000.sqlite3` unter SQLite), meldet Pfad, Größe und Dauer und räumt
danach alte Sicherungen weg. Geht etwas schief, endet es mit einem
Rückgabewert ungleich null — ein Timer oder Cronjob merkt den Ausfall also.

| Einstellung | Bedeutung |
|---|---|
| `BACKUP_DIR` | Zielverzeichnis, wird mit Rechten 0700 angelegt |
| `BACKUP_KEEP` | so viele Sicherungen bleiben liegen, 0 heißt unbegrenzt |
| `BACKUP_KEEP_DAYS` | zusätzliche Altersgrenze in Tagen, 0 schaltet sie ab |
| `BACKUP_PG_DUMP`, `BACKUP_PSQL` | nur nötig, wenn die Werkzeuge nicht im `PATH` stehen |

Beide Grenzen gelten nebeneinander, und die jeweils neueste Sicherung wird nie
entfernt: eine zu knapp eingestellte Altersgrenze soll nicht den letzten Stand
wegräumen. Aufgeräumt werden ausschließlich Dateien im Zielverzeichnis, deren
Name zum Schema passt; alles andere bleibt liegen.

Die Dateien gehören nur dem Benutzer (0600). Sie enthalten sämtliche
personenbezogenen Daten der Zeiterfassung und sind entsprechend zu behandeln.

**Was die Sicherung nicht enthält.** Nur die Datenbank. Die Konfiguration ist
getrennt zu sichern, sonst steht man mit einem Dump da, den man nicht
einspielen kann:

* `~/.config/zeiterfassung/zeiterfassung.env`
* die Podman-Secrets (`zeiterfassung-django-secret-key`,
  `zeiterfassung-database-url`, `zeiterfassung-db-password`) — sie lassen sich
  nicht auslesen, sie gehören in den Passwortspeicher
* die Unit-Dateien, falls sie von denen im Repository abweichen

Der `DJANGO_SECRET_KEY` gehört dazu: mit einem anderen Schlüssel sind nach dem
Rückspielen alle Sitzungen ungültig.

### Verzeichnis und Timer einrichten

Gesichert wird in ein Verzeichnis auf dem Host, damit die Sicherung einen
verlorenen Container überlebt. Rootless muss es dem Benutzer aus dem Container
gehören:

```bash
mkdir -p ~/zeiterfassung-sicherungen
podman unshare chown 10001:10001 ~/zeiterfassung-sicherungen
chmod 700 ~/zeiterfassung-sicherungen

install -Dm644 -t ~/.config/containers/systemd/ \
    deploy/quadlet/zeiterfassung-backup.container
install -Dm644 -t ~/.config/systemd/user/ \
    deploy/quadlet/zeiterfassung-backup.timer

systemctl --user daemon-reload
systemctl --user enable --now zeiterfassung-backup.timer
```

Der Timer läuft nachts um halb drei und holt einen ausgefallenen Lauf nach.
Von Hand auslösen und nachsehen:

```bash
systemctl --user start zeiterfassung-backup.service
journalctl --user -u zeiterfassung-backup --since today
ls -l ~/zeiterfassung-sicherungen
```

Das Verzeichnis liegt weiterhin auf demselben Server wie die Datenbank. Eine
Sicherung, die denselben Plattenschaden abbekommt, hilft nicht: von dort aus
gehört sie regelmäßig weg, etwa mit `rsync` oder `restic` auf einen anderen
Rechner.

### Zurückspielen

```bash
systemctl --user stop zeiterfassung-web.service zeiterfassung-scheduler.timer

podman run --rm -it \
    --network zeiterfassung.network \
    --env-file ~/.config/zeiterfassung/zeiterfassung.env \
    --env DJANGO_SECRET_KEY_FILE=/run/secrets/django-secret-key \
    --env DATABASE_URL_FILE=/run/secrets/database-url \
    --env BACKUP_DIR=/sicherungen \
    --secret zeiterfassung-django-secret-key,type=mount,target=django-secret-key \
    --secret zeiterfassung-database-url,type=mount,target=database-url \
    --volume ~/zeiterfassung-sicherungen:/sicherungen:Z \
    localhost/zeiterfassung:latest \
    python manage.py restore_database

systemctl --user start zeiterfassung-web.service zeiterfassung-scheduler.timer
```

Ohne `--file` wird die neueste Sicherung im Verzeichnis genommen; mit
`--file /sicherungen/zeiterfassung-20260921-023000.sql.gz` eine bestimmte.
Vor dem Überschreiben zeigt das Kommando Datei, Größe und Ziel und will ein
`ja` hören. Für ein Skript gibt es `--noinput`, dann läuft es ohne Rückfrage
durch — das ist der Schalter, mit dem man sich den Datenbestand still
überschreibt, also mit Bedacht.

Den Webdienst vorher anzuhalten ist wichtig: unter PostgreSQL wird der Dump in
einer einzigen Transaktion eingespielt (`ON_ERROR_STOP`,
`--single-transaction`), und was währenddessen noch gestempelt wird, ist
danach weg.

Stammt die Sicherung aus einer älteren Version, fehlen anschließend die
neueren Migrationen. Der Webcontainer wendet sie beim nächsten Start selbst
an; von Hand geht es mit `podman exec zeiterfassung-web python manage.py
migrate`.

### Einmal ausprobieren

Eine Sicherung, deren Rückspielen noch nie jemand versucht hat, ist eine
Vermutung. Der Durchgang dauert ein paar Minuten:

1. `systemctl --user start zeiterfassung-backup.service` und nachsehen, dass
   eine Datei mit plausibler Größe entstanden ist.
2. Im Tool etwas erfassen, das man wiedererkennt — eine Stempelung mit einer
   auffälligen Uhrzeit.
3. Die Sicherung von Schritt 1 zurückspielen.
4. Nachsehen, dass die Stempelung aus Schritt 2 wieder weg ist. Dann hat das
   Rückspielen wirklich funktioniert und nicht nur der Befehl.

Wer das nicht im Betrieb machen will, macht es auf einem zweiten Rechner mit
einer Kopie der Sicherung. Dann taugt der Durchgang gleich als Probe für den
Ernstfall, in dem der erste Rechner nicht mehr da ist.

### Ohne Podman

Dieselben Kommandos laufen unter Compose und in einer gewöhnlichen
Installation:

```bash
docker compose exec web python manage.py backup_database
python manage.py backup_database --dir /srv/sicherungen --keep 30
python manage.py restore_database --file /srv/sicherungen/zeiterfassung-20260921-023000.sql.gz
```

`pg_dump` und `psql` müssen dort vorhanden sein, wo das Kommando läuft, und
mindestens so neu sein wie der Server. Das Image bringt sie mit (Bauargument
`POSTGRES_CLIENT_VERSION`, Vorgabe 16). In der Entwicklung mit SQLite werden
keine externen Werkzeuge gebraucht: dort wird mit `VACUUM INTO` gesichert, was
auch dann einen stimmigen Stand ergibt, wenn nebenbei geschrieben wird.

Das Passwort der Datenbank steht bei keinem der beiden Kommandos in der
Kommandozeile und damit nicht in der Prozessliste: es wird `pg_dump` und
`psql` über eine kurzlebige Datei mit Rechten 0600 gereicht (`PGPASSFILE`).
Auch in der Ausgabe taucht es nicht auf; eine Fehlermeldung der Werkzeuge wird
vorher geschwärzt.

## Statt rootless als root

Die Units funktionieren auch systemweit. Dann gehören die Container-Units nach
`/etc/containers/systemd/`, der Timer nach `/etc/systemd/system/`, die Befehle
laufen mit `sudo` und ohne `--user`, und `%h` in `EnvironmentFile=` zeigt auf
`/root`. Sinnvoller ist in dem Fall ein fester Pfad:

```ini
EnvironmentFile=/etc/zeiterfassung/zeiterfassung.env
```

Lingering wird dann nicht gebraucht, Ports unter 1024 sind möglich.

## Bekannte Grenzen

* Die Units gehen von einem lokal gebauten Image aus
  (`localhost/zeiterfassung:latest`). Wer aus einer Registry zieht, trägt dort
  den vollen Namen ein und kann mit `AutoUpdate=registry` und
  `podman-auto-update.timer` automatisch aktualisieren.
* Es gibt keinen Reverse Proxy in diesem Aufbau. TLS, HTTP/2 und die
  Weiterleitung auf `127.0.0.1:8000` macht nginx, Caddy oder Traefik auf dem
  Host.
* Mit Podman 5 kann `Notify=healthy` in `zeiterfassung-db.container` die Unit
  erst als gestartet melden, wenn der Healthcheck grün ist. Die Zeile fehlt
  absichtlich, weil sie bei älteren Podman-Versionen die Unit gar nicht erst
  erzeugt; nötig ist sie nicht, weil der Webcontainer selbst auf die Datenbank
  wartet.
