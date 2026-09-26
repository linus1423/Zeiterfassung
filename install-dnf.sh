#!/usr/bin/env bash
# Installiert die Zeiterfassung auf Fedora, AlmaLinux, Rocky oder RHEL.
#
# Aufruf direkt nach dem Klonen, im Verzeichnis des Repositorys:
#
#   git clone https://github.com/linus1423/Zeiterfassung.git
#   cd Zeiterfassung
#   sudo ./install-dnf.sh
#
# Der Aufbau ist der aus docs/podman-quadlet.md: rootless Podman unter einem
# eigenen Dienstbenutzer, gestartet von systemd über Quadlet. Das Skript
# erledigt alle Schritte der Anleitung:
#
#   1. Pakete mit dnf installieren (podman, openssl, curl, optional nginx)
#   2. Dienstbenutzer mit Subuid-Bereich und Lingering anlegen
#   3. Quellen in ein Verzeichnis des Dienstbenutzers kopieren, Image bauen
#   4. Geheimnisse als "podman secret" erzeugen oder abfragen
#   5. Konfiguration schreiben, Units einsetzen, Dienste und Timer starten
#   6. optional nginx als Reverse Proxy mit TLS, Firewall und SELinux
#   7. optional ein System-Admin-Konto anlegen
#
# Erneut aufgerufen spielt es eine neue Version ein: vorhandene Geheimnisse
# und Einstellungen bleiben stehen und werden als Vorgabe angeboten.
#
# Ohne Rückfragen (zum Beispiel aus einer Automatisierung): --ja. Dann gelten
# die Vorgaben, überschreibbar mit den Umgebungsvariablen aus --hilfe.

set -Eeuo pipefail

readonly IMAGE="localhost/zeiterfassung:latest"
readonly MIN_PODMAN="4.7"
# Die Antworten des letzten Laufs, als Vorgaben für den nächsten. Gehört root:
# das Skript läuft als root und liest nichts ein, was der Dienstbenutzer
# schreiben kann, außer als reine Vorgabe, die danach geprüft wird.
readonly GEMERKT="/etc/zeiterfassung/install.conf"
# Liegt im Quellverzeichnis, damit ein weiterer Lauf nur ein Verzeichnis
# leert, das er selbst befüllt hat.
readonly QUELLEN_MARKE=".von-install-dnf"

QUELLVERZEICHNIS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly QUELLVERZEICHNIS

NICHT_INTERAKTIV=false
DIENST_UID=""
DIENST_GID=""
ENV_INHALT=""
GEMERKT_INHALT=""

# --- Ausgabe ----------------------------------------------------------------

if [ -t 1 ]; then
    FETT=$'\e[1m'
    GRUEN=$'\e[32m'
    GELB=$'\e[33m'
    ROT=$'\e[31m'
    NORMAL=$'\e[0m'
else
    FETT="" GRUEN="" GELB="" ROT="" NORMAL=""
fi

schritt() { printf '\n%s==> %s%s\n' "$FETT" "$1" "$NORMAL"; }
info() { printf '    %s\n' "$1"; }
ok() { printf '    %s✓%s %s\n' "$GRUEN" "$NORMAL" "$1"; }
warnung() { printf '    %s!%s %s\n' "$GELB" "$NORMAL" "$1" >&2; }
fehler() {
    printf '%sFehler:%s %s\n' "$ROT" "$NORMAL" "$1" >&2
    exit 1
}

# Nur in der obersten Shell melden, sonst steht jeder Abbruch aus einer
# Subshell doppelt da.
trap '[ "$BASH_SUBSHELL" -eq 0 ] && printf "%sAbbruch%s in Zeile %s: %s\n" "$ROT" "$NORMAL" "$LINENO" "$BASH_COMMAND" >&2' ERR

hilfe() {
    cat <<'EOF'
Aufruf: sudo ./install-dnf.sh [--ja] [--hilfe]

  --ja      keine Rückfragen, es gelten die Vorgaben bzw. die
            Umgebungsvariablen unten
  --hilfe   diese Hilfe

Vorgaben lassen sich über Umgebungsvariablen setzen, etwa
"sudo ZE_DOMAIN=zeit.example.org ./install-dnf.sh":

  BENUTZER               Dienstbenutzer                    (zeiterfassung)
  ZE_QUELLEN             Quellen für den Bau des Images    (~/zeiterfassung)
  ZE_SICHERUNGEN         Verzeichnis der Sicherungen       (~/zeiterfassung-sicherungen)
  ZE_DATENBANK           Verzeichnis der Datenbank; leer = benanntes Podman-Volume
  ZE_PORT                lokaler Port des Webdienstes      (8000)
  ZE_DOMAIN              öffentlicher Hostname             (hostname -f)
  ZE_MAIL                Mailversand einrichten (ja/nein)  (nein)
  ZE_NGINX               nginx als Reverse Proxy (ja/nein) (ja)
  ZE_TLS_ZERTIFIKAT      Zertifikat für nginx; leer = selbstsigniert erzeugen
  ZE_TLS_SCHLUESSEL      privater Schlüssel dazu
  ZE_ADMIN_ANLEGEN       System-Admin-Konto anlegen (ja/nein) (ja, mit --ja: nein)

  KEYCLOAK_CLIENT_ID, KEYCLOAK_SERVER_URL, KEYCLOAK_CLIENT_SECRET
  ENTRA_CLIENT_ID, ENTRA_TENANT_ID, ENTRA_CLIENT_SECRET
  DJANGO_EMAIL_HOST, DJANGO_EMAIL_PORT, DJANGO_EMAIL_HOST_USER,
  DJANGO_EMAIL_HOST_PASSWORD, DJANGO_DEFAULT_FROM_EMAIL

Geheimnisse, die nicht vorgegeben sind, werden erzeugt (Datenbankpasswort,
DJANGO_SECRET_KEY) oder verdeckt abgefragt (Client-Secrets, Mailpasswort).
Ein zweiter Lauf bietet die Antworten des ersten als Vorgabe an.
EOF
}

# --- Eingaben ---------------------------------------------------------------

# frage VAR "Text" "Vorgabe" [Prüffunktion]
# Eine gesetzte Umgebungsvariable VAR ersetzt die Vorgabe.
frage() {
    local var="$1" text="$2" vorgabe="${!1:-$3}" pruefung="${4:-}" antwort
    while true; do
        if $NICHT_INTERAKTIV; then
            antwort="$vorgabe"
        else
            read -r -p "    $text [${vorgabe}]: " antwort || fehler "Eingabe abgebrochen."
            antwort="${antwort:-$vorgabe}"
        fi
        if [ -z "$pruefung" ] || "$pruefung" "$antwort"; then
            printf -v "$var" '%s' "$antwort"
            return 0
        fi
        $NICHT_INTERAKTIV && fehler "Ungültiger Wert für $var: '$antwort'"
    done
}

# frage_geheim VAR "Text" – verdeckte Eingabe, leer ist erlaubt.
frage_geheim() {
    local var="$1" text="$2" antwort="${!1:-}"
    if ! $NICHT_INTERAKTIV && [ -z "$antwort" ]; then
        read -r -s -p "    $text: " antwort || fehler "Eingabe abgebrochen."
        printf '\n' >&2
    fi
    case "$antwort" in
        *$'\n'* | *$'\r'*) fehler "Das Geheimnis darf keinen Zeilenumbruch enthalten." ;;
    esac
    printf -v "$var" '%s' "$antwort"
}

# ja_nein "Text" "ja|nein" [VAR] – Rückgabe 0 bei ja. Eine gesetzte
# Umgebungsvariable VAR ersetzt die Vorgabe.
ja_nein() {
    local text="$1" vorgabe="$2" var="${3:-}" antwort
    if [ -n "$var" ] && [ -n "${!var:-}" ]; then
        vorgabe="${!var}"
    fi
    if $NICHT_INTERAKTIV; then
        antwort="$vorgabe"
    else
        local hinweis="j/N"
        [ "$vorgabe" = "ja" ] && hinweis="J/n"
        read -r -p "    $text [$hinweis]: " antwort || fehler "Eingabe abgebrochen."
        antwort="${antwort:-$vorgabe}"
    fi
    case "${antwort,,}" in
        j | ja | y | yes) return 0 ;;
        *) return 1 ;;
    esac
}

# Werte landen in Unit-Dateien, in der env-Datei (ohne Anführungszeichen) und
# in der nginx-Konfiguration. Deshalb eng prüfen statt später zu maskieren.
pruefe_benutzer() {
    [[ "$1" =~ ^[a-z_][a-z0-9_-]{0,30}$ && "$1" != root ]] ||
        { warnung "Nur Kleinbuchstaben, Ziffern, _ und -, nicht root."; return 1; }
}
pruefe_pfad() {
    [[ "$1" =~ ^/[A-Za-z0-9._/-]*[A-Za-z0-9._-]$ && "$1" != *..* ]] ||
        { warnung "Absoluter Pfad ohne Leerzeichen, Sonderzeichen und / am Ende erwartet."; return 1; }
}
pruefe_pfad_leer() { [ -z "$1" ] || pruefe_pfad "$1"; }
pruefe_port() {
    [[ "$1" =~ ^[0-9]{4,5}$ ]] && [ "$1" -ge 1024 ] && [ "$1" -le 65535 ] ||
        { warnung "Port zwischen 1024 und 65535 erwartet."; return 1; }
}
pruefe_domain() {
    [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] ||
        { warnung "Hostname wie zeiterfassung.example.org erwartet."; return 1; }
}
pruefe_url() {
    [[ "$1" =~ ^https://[^[:space:]\"\'\\]+$ ]] ||
        { warnung "URL mit https:// erwartet."; return 1; }
}
pruefe_wert() {
    [[ "$1" =~ ^[^[:space:]\"\'\\]*$ ]] ||
        { warnung "Keine Leerzeichen, Anführungszeichen oder Backslashes."; return 1; }
}
pruefe_text() {
    [[ "$1" != *$'\n'* && "$1" != *\"* && "$1" != *\\* ]] ||
        { warnung "Keine Anführungszeichen oder Backslashes."; return 1; }
}
pruefe_zahl() { [[ "$1" =~ ^[0-9]+$ ]] || { warnung "Zahl erwartet."; return 1; }; }

# wert_aus TEXT KEY [VORGABE] – der letzte Wert KEY=... aus TEXT.
wert_aus() {
    local zeile
    zeile="$(printf '%s\n' "$1" | grep -E "^${2}=" | tail -n 1 || true)"
    if [ -n "$zeile" ]; then
        printf '%s' "${zeile#*=}"
    else
        printf '%s' "${3:-}"
    fi
}

# Werte aus der bestehenden env-Datei bzw. dem letzten Lauf, damit ein zweiter
# Lauf sie als Vorgabe anbietet.
bestehend() { wert_aus "$ENV_INHALT" "$1" "${2:-}"; }
gemerkt() { wert_aus "$GEMERKT_INHALT" "$1" "${2:-}"; }

# setze DATEI KEY WERT – ersetzt die Zeile KEY=... oder hängt sie an.
setze() {
    local datei="$1" key="$2" wert="$3" tmp
    tmp="$(mktemp)"
    K="$key" W="$wert" awk '
        BEGIN { k = ENVIRON["K"]; w = ENVIRON["W"]; gefunden = 0 }
        index($0, k "=") == 1 { if (!gefunden) print k "=" w; gefunden = 1; next }
        { print }
        END { if (!gefunden) print k "=" w }
    ' "$datei" >"$tmp"
    cat "$tmp" >"$datei"
    rm -f "$tmp"
}

# ersetze_zeile DATEI PRAEFIX NEUE_ZEILE – ersetzt jede Zeile, die mit PRAEFIX
# beginnt. Bricht ab, wenn es keine gibt: dann passt die Unit nicht mehr zum
# Skript, und still eine falsche Konfiguration zu erzeugen wäre schlimmer.
ersetze_zeile() {
    local datei="$1" praefix="$2" neu="$3" tmp
    grep -q "^${praefix}" "$datei" || fehler "In $datei fehlt eine Zeile '${praefix}…'."
    tmp="$(mktemp)"
    P="$praefix" N="$neu" awk '
        index($0, ENVIRON["P"]) == 1 { print ENVIRON["N"]; next }
        { print }
    ' "$datei" >"$tmp"
    cat "$tmp" >"$datei"
    rm -f "$tmp"
}

# fuege_nach DATEI PRAEFIX ZEILEN – fügt ZEILEN nach der ersten Zeile ein, die
# mit PRAEFIX beginnt.
fuege_nach() {
    local datei="$1" praefix="$2" zeilen="$3" tmp
    [ -n "$zeilen" ] || return 0
    grep -q "^${praefix}" "$datei" || fehler "In $datei fehlt eine Zeile '${praefix}…'."
    tmp="$(mktemp)"
    P="$praefix" Z="$zeilen" awk '
        { print }
        !fertig && index($0, ENVIRON["P"]) == 1 { printf "%s", ENVIRON["Z"]; fertig = 1 }
    ' "$datei" >"$tmp"
    cat "$tmp" >"$datei"
    rm -f "$tmp"
}

zufall() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1" || true; }

# version_ab IST MINDESTENS
version_ab() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# --- Befehle als Dienstbenutzer ---------------------------------------------

# Rootless Podman und "systemctl --user" brauchen die Laufzeitumgebung des
# Benutzers; runuser allein setzt sie nicht. "env -i", damit abgefragte oder
# übergebene Geheimnisse (KEYCLOAK_CLIENT_SECRET usw.) nicht in der Umgebung
# der Prozesse des Dienstbenutzers landen.
als_dienst() {
    (
        cd "$DIENST_HOME"
        runuser -u "$BENUTZER" -- env -i \
            PATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin \
            LANG="${LANG:-C.UTF-8}" \
            TERM="${TERM:-dumb}" \
            USER="$BENUTZER" \
            LOGNAME="$BENUTZER" \
            HOME="$DIENST_HOME" \
            XDG_RUNTIME_DIR="/run/user/$DIENST_UID" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$DIENST_UID/bus" \
            "$@"
    )
}

# schreibe_als_dienst ZIEL MODUS – schreibt die Standardeingabe als
# Dienstbenutzer nach ZIEL. Alles im Home des Dienstbenutzers wird so
# geschrieben und nicht als root: root würde dort einem Symlink folgen, den
# der Benutzer gelegt hat, und die Datei dahinter überschreiben.
schreibe_als_dienst() {
    # shellcheck disable=SC2016 # $1 und $2 gehören der inneren Shell.
    als_dienst sh -c 'umask 077 && cat >"$1.neu" && chmod "$2" "$1.neu" && mv -f "$1.neu" "$1"' \
        schreibe "$1" "$2"
}

# verzeichnis PFAD – legt PFAD für den Dienstbenutzer an (Rechte 0700).
verzeichnis() {
    local pfad="$1"
    case "$pfad/" in
        "$DIENST_HOME"/*) als_dienst mkdir -p -m 700 "$pfad" ;;
        *)
            # Außerhalb des Homes legt root die Elternverzeichnisse an, lesbar,
            # damit der Dienstbenutzer hindurch darf.
            (umask 022 && mkdir -p "$(dirname "$pfad")")
            [ -L "$pfad" ] && fehler "$pfad ist ein Symlink, bitte ein echtes Verzeichnis angeben."
            install -d -m 700 -o "$BENUTZER" -g "$DIENST_GID" "$pfad"
            ;;
    esac
}

secret_da() { als_dienst podman secret inspect "$1" >/dev/null 2>&1; }

# secret_setzen NAME WERT – legt das Geheimnis an oder ersetzt es. Der Wert
# geht über die Standardeingabe, nicht über die Kommandozeile, damit er nicht
# in der Prozessliste steht.
secret_setzen() {
    local name="$1" wert="$2"
    if secret_da "$name"; then
        als_dienst podman secret rm "$name" >/dev/null
    fi
    printf '%s' "$wert" | als_dienst podman secret create "$name" - >/dev/null
}

# client_secret NAME TEXT VAR – fragt ein Geheimnis ab; leer lässt ein
# vorhandenes stehen. Rückgabe 0, wenn danach ein Geheimnis existiert.
client_secret() {
    local name="$1" text="$2" var="$3"
    if secret_da "$name"; then
        frage_geheim "$var" "$text (leer = bisheriges behalten)"
    else
        frage_geheim "$var" "$text"
    fi
    if [ -n "${!var}" ]; then
        secret_setzen "$name" "${!var}"
        printf -v "$var" '%s' ""
        ok "Geheimnis $name gespeichert"
    fi
    secret_da "$name"
}

# --- Ablauf -----------------------------------------------------------------

argumente() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --ja | -y) NICHT_INTERAKTIV=true ;;
            --hilfe | -h | --help)
                hilfe
                exit 0
                ;;
            *) fehler "Unbekanntes Argument: $1 (siehe --hilfe)" ;;
        esac
        shift
    done
}

voraussetzungen() {
    schritt "Voraussetzungen prüfen"
    [ "$(id -u)" -eq 0 ] || fehler "Bitte mit sudo aufrufen: sudo $0"
    [ -f "$QUELLVERZEICHNIS/Dockerfile" ] && [ -d "$QUELLVERZEICHNIS/deploy/quadlet" ] ||
        fehler "Das Skript muss im geklonten Repository liegen ($QUELLVERZEICHNIS)."
    command -v dnf >/dev/null || fehler "dnf nicht gefunden. Dieses Skript ist für Fedora und AlmaLinux."

    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" fedora "* | *" rhel "* | *" almalinux "* | *" centos "*) ok "${PRETTY_NAME:-$ID}" ;;
        *) warnung "Nicht getestet auf ${PRETTY_NAME:-unbekannt}, es geht trotzdem weiter." ;;
    esac
    if ! $NICHT_INTERAKTIV && [ ! -t 0 ]; then
        fehler "Keine Eingabe möglich. Im Terminal aufrufen oder --ja angeben."
    fi
}

abfragen() {
    schritt "Einstellungen"
    info "Enter übernimmt den Wert in eckigen Klammern."
    printf '\n'

    [ -f "$GEMERKT" ] && GEMERKT_INHALT="$(cat "$GEMERKT")"
    frage BENUTZER "Dienstbenutzer (rootless Podman)" "$(gemerkt BENUTZER zeiterfassung)" pruefe_benutzer
    if id "$BENUTZER" >/dev/null 2>&1; then
        DIENST_HOME="$(getent passwd "$BENUTZER" | cut -d: -f6)"
        DIENST_UID="$(id -u "$BENUTZER")"
        [ "$DIENST_UID" -ne 0 ] || fehler "Der Dienstbenutzer darf nicht UID 0 haben."
    else
        DIENST_HOME="/home/$BENUTZER"
    fi
    ENV_DATEI="$DIENST_HOME/.config/zeiterfassung/zeiterfassung.env"
    if [ -n "$DIENST_UID" ]; then
        ENV_INHALT="$(als_dienst cat "$ENV_DATEI" 2>/dev/null || true)"
    fi

    frage ZE_QUELLEN "Verzeichnis für die Quellen (Bau des Images)" \
        "$(gemerkt ZE_QUELLEN "$DIENST_HOME/zeiterfassung")" pruefe_pfad
    frage ZE_SICHERUNGEN "Verzeichnis für die nächtlichen Sicherungen" \
        "$(gemerkt ZE_SICHERUNGEN "$DIENST_HOME/zeiterfassung-sicherungen")" pruefe_pfad
    info "Die Datenbank liegt ohne Angabe in einem benannten Podman-Volume"
    info "unter $DIENST_HOME/.local/share/containers/storage/volumes/."
    frage ZE_DATENBANK "Eigenes Verzeichnis für die Datenbank (leer = Volume)" \
        "$(gemerkt ZE_DATENBANK)" pruefe_pfad_leer
    frage ZE_PORT "Lokaler Port des Webdienstes (nur 127.0.0.1)" "$(gemerkt ZE_PORT 8000)" pruefe_port

    # Das Quellverzeichnis wird bei jedem Lauf geleert. Es darf deshalb nichts
    # enthalten, was bleiben muss: nicht das Home, nicht die Sicherungen, nicht
    # die Datenbank, nicht den Klon.
    local p
    for p in "$DIENST_HOME" "$ZE_SICHERUNGEN" "$ZE_DATENBANK" "$QUELLVERZEICHNIS" "$(dirname "$ENV_DATEI")"; do
        [ -n "$p" ] || continue
        case "$p/" in
            "$ZE_QUELLEN"/*) fehler "$p liegt im Quellverzeichnis $ZE_QUELLEN, das bei jedem Lauf geleert wird." ;;
        esac
    done
    # Geleert wird auch sonst nur, was das Skript selbst befüllt hat.
    if [ -d "$ZE_QUELLEN" ] && [ ! -f "$ZE_QUELLEN/$QUELLEN_MARKE" ] &&
        [ -n "$(find "$ZE_QUELLEN" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        fehler "$ZE_QUELLEN ist nicht leer und stammt nicht von diesem Skript. Bitte ein anderes Verzeichnis angeben."
    fi

    local host bisher_domain
    host="$(hostname -f 2>/dev/null || cat /proc/sys/kernel/hostname)"
    bisher_domain="$(bestehend DJANGO_ALLOWED_HOSTS "$host")"
    frage ZE_DOMAIN "Öffentlicher Hostname" "${bisher_domain%%,*}" pruefe_domain

    printf '\n'
    info "Anmeldung über Keycloak (leer lassen, wenn nicht benutzt)."
    frage KEYCLOAK_CLIENT_ID "Keycloak Client-ID" "$(bestehend KEYCLOAK_CLIENT_ID)" pruefe_wert
    if [ -n "$KEYCLOAK_CLIENT_ID" ]; then
        local kc_url
        kc_url="$(bestehend KEYCLOAK_SERVER_URL)"
        [[ "$kc_url" == *example.com* ]] && kc_url=""
        frage KEYCLOAK_SERVER_URL "Keycloak-Realm-URL (https://…/realms/<realm>)" "$kc_url" pruefe_url
        frage KEYCLOAK_DISPLAY_NAME "Beschriftung des Knopfs" \
            "$(bestehend KEYCLOAK_DISPLAY_NAME Keycloak)" pruefe_text
    fi

    printf '\n'
    info "Anmeldung über Microsoft Entra ID (leer lassen, wenn nicht benutzt)."
    frage ENTRA_CLIENT_ID "Entra Anwendungs-ID (Client-ID)" "$(bestehend ENTRA_CLIENT_ID)" pruefe_wert
    if [ -n "$ENTRA_CLIENT_ID" ]; then
        frage ENTRA_TENANT_ID "Entra Verzeichnis-ID (Tenant-ID)" "$(bestehend ENTRA_TENANT_ID)" pruefe_wert
        frage ENTRA_DISPLAY_NAME "Beschriftung des Knopfs" \
            "$(bestehend ENTRA_DISPLAY_NAME "Microsoft Entra ID")" pruefe_text
    fi
    if [ -z "$KEYCLOAK_CLIENT_ID" ] && [ -z "$ENTRA_CLIENT_ID" ]; then
        warnung "Kein Identity-Provider: anmelden geht dann nur unter /admin/ mit Passwort."
    fi

    printf '\n'
    MAIL=false
    local mail_vorgabe=nein
    [ "$(bestehend CORRECTION_EMAILS_ENABLED false)" = "true" ] && mail_vorgabe=ja
    if ja_nein "Mailversand einrichten (Korrekturen, Erinnerungen, Exporte)?" "$mail_vorgabe" ZE_MAIL; then
        MAIL=true
        local mail_host
        mail_host="$(bestehend DJANGO_EMAIL_HOST)"
        [ "$mail_host" = "smtp.example.com" ] && mail_host=""
        frage DJANGO_EMAIL_HOST "SMTP-Server" "$mail_host" pruefe_wert
        [ -n "$DJANGO_EMAIL_HOST" ] || fehler "Ohne SMTP-Server kein Mailversand."
        frage DJANGO_EMAIL_PORT "SMTP-Port (STARTTLS)" "$(bestehend DJANGO_EMAIL_PORT 587)" pruefe_zahl
        frage DJANGO_EMAIL_HOST_USER "SMTP-Benutzer (leer = ohne Anmeldung)" \
            "$(bestehend DJANGO_EMAIL_HOST_USER)" pruefe_wert
        local absender
        absender="$(bestehend DJANGO_DEFAULT_FROM_EMAIL)"
        [[ -z "$absender" || "$absender" == *example.com ]] && absender="zeiterfassung@${ZE_DOMAIN}"
        frage DJANGO_DEFAULT_FROM_EMAIL "Absenderadresse" "$absender" pruefe_wert
    fi

    printf '\n'
    NGINX=false
    ZE_TLS_ZERTIFIKAT="${ZE_TLS_ZERTIFIKAT:-}"
    ZE_TLS_SCHLUESSEL="${ZE_TLS_SCHLUESSEL:-}"
    if ja_nein "nginx als Reverse Proxy mit HTTPS einrichten?" "$(gemerkt ZE_NGINX ja)" ZE_NGINX; then
        NGINX=true
        info "Zertifikat und Schlüssel im PEM-Format, z. B. aus Let's Encrypt."
        info "Leer lassen erzeugt ein selbstsigniertes Zertifikat zum späteren Ersetzen."
        frage ZE_TLS_ZERTIFIKAT "Pfad zum Zertifikat (mit Kette)" \
            "$(gemerkt ZE_TLS_ZERTIFIKAT)" pruefe_pfad_leer
        if [ -n "$ZE_TLS_ZERTIFIKAT" ]; then
            [ -r "$ZE_TLS_ZERTIFIKAT" ] || fehler "Zertifikat nicht lesbar: $ZE_TLS_ZERTIFIKAT"
            frage ZE_TLS_SCHLUESSEL "Pfad zum privaten Schlüssel" \
                "$(gemerkt ZE_TLS_SCHLUESSEL)" pruefe_pfad
            [ -r "$ZE_TLS_SCHLUESSEL" ] || fehler "Schlüssel nicht lesbar: $ZE_TLS_SCHLUESSEL"
        else
            ZE_TLS_SCHLUESSEL=""
        fi
    fi

    schritt "Zusammenfassung"
    info "Dienstbenutzer:  $BENUTZER ($DIENST_HOME)"
    info "Quellen:         $ZE_QUELLEN"
    info "Sicherungen:     $ZE_SICHERUNGEN"
    info "Datenbank:       ${ZE_DATENBANK:-Podman-Volume zeiterfassung-pgdata}"
    info "Webdienst:       127.0.0.1:$ZE_PORT"
    info "Adresse:         https://$ZE_DOMAIN/"
    info "Keycloak:        ${KEYCLOAK_CLIENT_ID:-aus}"
    info "Entra ID:        ${ENTRA_CLIENT_ID:-aus}"
    info "Mail:            $($MAIL && echo "$DJANGO_EMAIL_HOST" || echo aus)"
    info "nginx:           $($NGINX && echo ja || echo nein)"
    printf '\n'
    ja_nein "So installieren?" "ja" || fehler "Abgebrochen, nichts verändert."

    install -d -m 700 "$(dirname "$GEMERKT")"
    cat >"$GEMERKT" <<EOF
# Antworten von install-dnf.sh, Vorgaben für den nächsten Lauf.
BENUTZER=$BENUTZER
ZE_QUELLEN=$ZE_QUELLEN
ZE_SICHERUNGEN=$ZE_SICHERUNGEN
ZE_DATENBANK=$ZE_DATENBANK
ZE_PORT=$ZE_PORT
ZE_NGINX=$($NGINX && echo ja || echo nein)
ZE_TLS_ZERTIFIKAT=$ZE_TLS_ZERTIFIKAT
ZE_TLS_SCHLUESSEL=$ZE_TLS_SCHLUESSEL
EOF
    chmod 600 "$GEMERKT"
}

pakete() {
    schritt "Pakete installieren"
    local liste=(podman tar openssl curl)
    $NGINX && liste+=(nginx)
    dnf install -y "${liste[@]}"

    local version
    version="$(podman version --format '{{.Client.Version}}')"
    version_ab "$version" "$MIN_PODMAN" ||
        fehler "Podman $version ist zu alt, gebraucht wird mindestens $MIN_PODMAN (Quadlet mit Secret=)."
    [ -x /usr/libexec/podman/quadlet ] || fehler "Quadlet fehlt (/usr/libexec/podman/quadlet)."
    ok "Podman $version"
}

dienstbenutzer() {
    schritt "Dienstbenutzer $BENUTZER"
    if id "$BENUTZER" >/dev/null 2>&1; then
        ok "vorhanden"
    else
        # Ein gewöhnlicher Benutzer, kein --system: useradd vergibt nur dann
        # einen Subuid-Bereich, und ohne den läuft rootless Podman nicht.
        useradd --create-home --home-dir "$DIENST_HOME" --shell /sbin/nologin \
            --comment "Zeiterfassung (rootless Podman)" "$BENUTZER"
        ok "angelegt"
    fi
    DIENST_UID="$(id -u "$BENUTZER")"
    DIENST_GID="$(id -g "$BENUTZER")"

    local neu_zugeteilt=false datei start
    for datei in /etc/subuid /etc/subgid; do
        if ! grep -q "^${BENUTZER}:" "$datei" 2>/dev/null; then
            # Hinter dem höchsten vergebenen Bereich weitermachen.
            start="$(awk -F: 'BEGIN { m = 100000 } { e = $2 + $3; if (e > m) m = e } END { print m }' \
                "$datei" 2>/dev/null || echo 100000)"
            if [ "$datei" = /etc/subuid ]; then
                usermod --add-subuids "${start}-$((start + 65535))" "$BENUTZER"
            else
                usermod --add-subgids "${start}-$((start + 65535))" "$BENUTZER"
            fi
            neu_zugeteilt=true
        fi
    done
    if $neu_zugeteilt; then
        ok "Subuid- und Subgid-Bereich zugeteilt"
    fi

    # Ohne Lingering beendet systemd die Dienste, sobald sich der Benutzer
    # abmeldet, und startet sie nach einem Neustart erst beim nächsten Login.
    loginctl enable-linger "$BENUTZER"
    systemctl start "user@${DIENST_UID}.service"
    local _
    for _ in $(seq 1 30); do
        [ -S "/run/user/$DIENST_UID/bus" ] && break
        sleep 1
    done
    [ -S "/run/user/$DIENST_UID/bus" ] || fehler "systemd --user für $BENUTZER startet nicht."
    ok "Lingering an, systemd --user läuft"

    if $neu_zugeteilt; then
        als_dienst podman system migrate >/dev/null 2>&1 || true
    fi
}

image_bauen() {
    schritt "Quellen kopieren und Image bauen"
    verzeichnis "$ZE_QUELLEN"
    # root liest den Klon (der oft unter /root liegt), ausgepackt wird als
    # Dienstbenutzer in das geleerte Verzeichnis. Lokale Daten wie .env, eine
    # SQLite-Datenbank oder eine virtuelle Umgebung kommen nicht mit.
    # shellcheck disable=SC2016 # $1 und $2 gehören der inneren Shell.
    tar -C "$QUELLVERZEICHNIS" -cf - \
        --exclude=./.git --exclude=./.env --exclude=./.venv --exclude=./venv \
        --exclude='./*.sqlite3' --exclude=./backups --exclude=./staticfiles . |
        als_dienst sh -c 'find "$1" -mindepth 1 -delete && tar -C "$1" --no-same-owner -xf - && touch "$1/$2"' \
            auspacken "$ZE_QUELLEN" "$QUELLEN_MARKE"
    ok "nach $ZE_QUELLEN kopiert"

    info "Der erste Bau lädt Python, die PostgreSQL-Werkzeuge und die Abhängigkeiten,"
    info "das dauert einige Minuten."
    als_dienst podman build --pull=newer -t "$IMAGE" "$ZE_QUELLEN"
    als_dienst podman pull -q docker.io/library/postgres:16 >/dev/null
    ok "Image $IMAGE gebaut"
}

geheimnisse() {
    schritt "Geheimnisse"

    # Datenbankpasswort und DATABASE_URL gehören zusammen: das Passwort legt
    # PostgreSQL beim ersten Start im Datenverzeichnis fest, ein neues passt
    # danach nicht mehr. Deshalb nie ersetzen, wenn schon eins da ist.
    if secret_da zeiterfassung-db-password; then
        secret_da zeiterfassung-database-url ||
            fehler "zeiterfassung-db-password ist da, zeiterfassung-database-url fehlt. Bitte von Hand anlegen (docs/podman-quadlet.md)."
        ok "Datenbankpasswort vorhanden, bleibt"
    else
        if [ -z "$ZE_DATENBANK" ] && als_dienst podman volume exists zeiterfassung-pgdata; then
            warnung "Das Volume zeiterfassung-pgdata existiert schon, das Passwort darin"
            warnung "passt nicht zum neu erzeugten. Bei einer echten Neuinstallation vorher"
            warnung "als $BENUTZER: podman volume rm zeiterfassung-pgdata (löscht die Datenbank!)"
        fi
        # Nur Buchstaben und Ziffern: das Passwort steht gleich in einer URL.
        local pw
        pw="$(zufall 40)"
        [ ${#pw} -eq 40 ] || fehler "Zufallswert konnte nicht erzeugt werden."
        secret_setzen zeiterfassung-db-password "$pw"
        secret_setzen zeiterfassung-database-url \
            "postgres://zeiterfassung:${pw}@zeiterfassung-db:5432/zeiterfassung"
        pw=""
        ok "Datenbankpasswort erzeugt"
    fi

    if secret_da zeiterfassung-django-secret-key; then
        ok "DJANGO_SECRET_KEY vorhanden, bleibt"
    else
        local key
        key="$(openssl rand -base64 48 | tr -d '\n')"
        [ ${#key} -eq 64 ] || fehler "Zufallswert konnte nicht erzeugt werden."
        secret_setzen zeiterfassung-django-secret-key "$key"
        key=""
        ok "DJANGO_SECRET_KEY erzeugt"
    fi

    # Zusätzliche Secret-Zeilen für die Units, je nach Konfiguration.
    SECRETS_WEB=""
    SECRETS_MAIL=""
    if [ -n "$KEYCLOAK_CLIENT_ID" ]; then
        client_secret zeiterfassung-keycloak-secret "Keycloak Client-Secret" KEYCLOAK_CLIENT_SECRET ||
            fehler "Ohne Client-Secret funktioniert die Anmeldung über Keycloak nicht."
        SECRETS_WEB+=$'Environment=KEYCLOAK_CLIENT_SECRET_FILE=/run/secrets/keycloak-secret\n'
        SECRETS_WEB+=$'Secret=zeiterfassung-keycloak-secret,type=mount,target=keycloak-secret\n'
    fi
    if [ -n "$ENTRA_CLIENT_ID" ]; then
        client_secret zeiterfassung-entra-secret "Entra Client-Secret (der Wert, nicht die ID)" \
            ENTRA_CLIENT_SECRET ||
            fehler "Ohne Client-Secret funktioniert die Anmeldung über Entra ID nicht."
        SECRETS_WEB+=$'Environment=ENTRA_CLIENT_SECRET_FILE=/run/secrets/entra-secret\n'
        SECRETS_WEB+=$'Secret=zeiterfassung-entra-secret,type=mount,target=entra-secret\n'
    fi
    if $MAIL && [ -n "$DJANGO_EMAIL_HOST_USER" ]; then
        client_secret zeiterfassung-email-password "SMTP-Passwort" DJANGO_EMAIL_HOST_PASSWORD ||
            fehler "Mit SMTP-Benutzer wird auch ein Passwort gebraucht."
        SECRETS_MAIL+=$'Environment=DJANGO_EMAIL_HOST_PASSWORD_FILE=/run/secrets/email-password\n'
        SECRETS_MAIL+=$'Secret=zeiterfassung-email-password,type=mount,target=email-password\n'
    fi
}

# shellcheck disable=SC2153 # Die Variablen setzt "frage" in abfragen.
konfiguration() {
    schritt "Konfiguration $ENV_DATEI"
    verzeichnis "$(dirname "$ENV_DATEI")"
    if [ -n "$ENV_INHALT" ]; then
        printf '%s\n' "$ENV_INHALT" | schreibe_als_dienst "$ENV_DATEI.bak" 600
        info "bisherige Datei gesichert als $ENV_DATEI.bak, eigene Änderungen bleiben"
    else
        ENV_INHALT="$(cat "$QUELLVERZEICHNIS/deploy/quadlet/zeiterfassung.env.example")"
    fi

    # Bearbeitet wird eine Kopie bei root, zurückgeschrieben als Dienstbenutzer.
    local f
    f="$(mktemp)"
    printf '%s\n' "$ENV_INHALT" >"$f"

    setze "$f" DJANGO_DEBUG false
    setze "$f" DJANGO_ALLOWED_HOSTS "$ZE_DOMAIN"
    setze "$f" DJANGO_CSRF_TRUSTED_ORIGINS "https://$ZE_DOMAIN"
    setze "$f" SITE_BASE_URL "https://$ZE_DOMAIN"
    # Der Webdienst ist nur über 127.0.0.1 erreichbar, also immer über einen
    # Proxy, ob nginx von hier oder ein eigener.
    setze "$f" DJANGO_BEHIND_PROXY true

    setze "$f" KEYCLOAK_CLIENT_ID "$KEYCLOAK_CLIENT_ID"
    if [ -n "$KEYCLOAK_CLIENT_ID" ]; then
        setze "$f" KEYCLOAK_SERVER_URL "$KEYCLOAK_SERVER_URL"
        setze "$f" KEYCLOAK_DISPLAY_NAME "$KEYCLOAK_DISPLAY_NAME"
    fi
    setze "$f" ENTRA_CLIENT_ID "$ENTRA_CLIENT_ID"
    if [ -n "$ENTRA_CLIENT_ID" ]; then
        setze "$f" ENTRA_TENANT_ID "$ENTRA_TENANT_ID"
        setze "$f" ENTRA_DISPLAY_NAME "$ENTRA_DISPLAY_NAME"
    fi

    local an=false
    $MAIL && an=true
    setze "$f" CORRECTION_EMAILS_ENABLED "$an"
    setze "$f" REMINDER_EMAILS_ENABLED "$an"
    setze "$f" EXPORT_EMAILS_ENABLED "$an"
    if $MAIL; then
        setze "$f" DJANGO_EMAIL_HOST "$DJANGO_EMAIL_HOST"
        setze "$f" DJANGO_EMAIL_PORT "$DJANGO_EMAIL_PORT"
        setze "$f" DJANGO_EMAIL_HOST_USER "$DJANGO_EMAIL_HOST_USER"
        setze "$f" DJANGO_DEFAULT_FROM_EMAIL "$DJANGO_DEFAULT_FROM_EMAIL"
    fi

    schreibe_als_dienst "$ENV_DATEI" 600 <"$f"
    rm -f "$f"
    ok "geschrieben"
}

datenverzeichnisse() {
    schritt "Verzeichnisse für Sicherungen und Datenbank"
    # Rootless gehören die Dateien im Container einer abgebildeten UID aus dem
    # Subuid-Bereich; "podman unshare chown" setzt genau diese. :Z in der Unit
    # kümmert sich um das SELinux-Label.
    verzeichnis "$ZE_SICHERUNGEN"
    als_dienst podman unshare chown 10001:10001 "$ZE_SICHERUNGEN"
    ok "$ZE_SICHERUNGEN (im Container UID 10001)"
    if [ -n "$ZE_DATENBANK" ]; then
        if als_dienst podman volume exists zeiterfassung-pgdata; then
            warnung "Es gibt noch das Volume zeiterfassung-pgdata. Die Daten darin werden"
            warnung "nicht nach $ZE_DATENBANK übernommen; dafür sichern und zurückspielen."
        fi
        verzeichnis "$ZE_DATENBANK"
        # 999 ist der Benutzer postgres im offiziellen Image.
        als_dienst podman unshare chown 999:999 "$ZE_DATENBANK"
        ok "$ZE_DATENBANK (im Container UID 999)"
    fi
}

units() {
    schritt "Units einsetzen"
    local quadlet_dir="$DIENST_HOME/.config/containers/systemd"
    local systemd_dir="$DIENST_HOME/.config/systemd/user"
    als_dienst mkdir -p "$quadlet_dir" "$systemd_dir"

    local tmp
    tmp="$(mktemp -d)"
    cp "$QUELLVERZEICHNIS"/deploy/quadlet/*.container \
        "$QUELLVERZEICHNIS"/deploy/quadlet/*.network \
        "$QUELLVERZEICHNIS"/deploy/quadlet/*.volume \
        "$QUELLVERZEICHNIS"/deploy/quadlet/*.timer "$tmp/"

    ersetze_zeile "$tmp/zeiterfassung-web.container" "PublishPort=" \
        "PublishPort=127.0.0.1:${ZE_PORT}:8000"
    ersetze_zeile "$tmp/zeiterfassung-backup.container" "Volume=" \
        "Volume=${ZE_SICHERUNGEN}:/sicherungen:Z"
    if [ -n "$ZE_DATENBANK" ]; then
        ersetze_zeile "$tmp/zeiterfassung-db.container" "Volume=" \
            "Volume=${ZE_DATENBANK}:/var/lib/postgresql/data:Z"
        rm -f "$tmp/zeiterfassung-pgdata.volume"
        als_dienst rm -f "$quadlet_dir/zeiterfassung-pgdata.volume"
    fi
    fuege_nach "$tmp/zeiterfassung-web.container" "Secret=zeiterfassung-database-url" \
        "${SECRETS_WEB}${SECRETS_MAIL}"
    fuege_nach "$tmp/zeiterfassung-scheduler.container" "Secret=zeiterfassung-database-url" \
        "$SECRETS_MAIL"

    local datei ziel
    for datei in "$tmp"/*; do
        case "$datei" in
            *.timer) ziel="$systemd_dir" ;;
            *) ziel="$quadlet_dir" ;;
        esac
        # shellcheck disable=SC2094 # Quelle bei root, Ziel beim Dienstbenutzer.
        schreibe_als_dienst "$ziel/$(basename "$datei")" 644 <"$datei"
    done
    rm -rf "$tmp"

    if ! als_dienst /usr/libexec/podman/quadlet -user -dryrun >/dev/null; then
        als_dienst /usr/libexec/podman/quadlet -user -dryrun || true
        fehler "Quadlet kann die Units nicht übersetzen, siehe oben."
    fi
    als_dienst systemctl --user daemon-reload
    ok "Units übersetzt"
}

starten() {
    schritt "Dienste starten"
    # Aus Quadlet erzeugte Units lassen sich nicht mit "systemctl enable"
    # einschalten; den Start beim Hochfahren regelt der Abschnitt [Install] in
    # der .container-Datei. Neu gestartet wird der Webdienst, damit ein
    # zweiter Lauf das neue Image übernimmt.
    als_dienst systemctl --user start zeiterfassung-db.service
    als_dienst systemctl --user restart zeiterfassung-web.service
    als_dienst systemctl --user enable --now \
        zeiterfassung-scheduler.timer zeiterfassung-backup.timer

    info "Warte auf den Webdienst (beim ersten Start mit Migrationen) …"
    local _
    for _ in $(seq 1 150); do
        if curl --fail --silent --output /dev/null "http://127.0.0.1:${ZE_PORT}/readyz"; then
            ok "Webdienst bereit auf 127.0.0.1:$ZE_PORT"
            return 0
        fi
        sleep 2
    done
    als_dienst journalctl --user -u zeiterfassung-web --no-pager -n 40 >&2 || true
    fehler "Der Webdienst ist nach 5 Minuten nicht bereit, siehe Protokoll oben."
}

nginx_einrichten() {
    $NGINX || return 0
    schritt "nginx als Reverse Proxy"
    local zert="$ZE_TLS_ZERTIFIKAT" schluessel="$ZE_TLS_SCHLUESSEL"
    if [ -z "$zert" ]; then
        zert="/etc/pki/tls/certs/zeiterfassung-selbstsigniert.crt"
        schluessel="/etc/pki/tls/private/zeiterfassung-selbstsigniert.key"
        if [ ! -f "$zert" ] || [ ! -f "$schluessel" ]; then
            (
                umask 077
                openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
                    -subj "/CN=${ZE_DOMAIN}" -addext "subjectAltName=DNS:${ZE_DOMAIN}" \
                    -keyout "$schluessel" -out "$zert" 2>/dev/null
            )
            chmod 644 "$zert"
        fi
        warnung "Selbstsigniertes Zertifikat: Browser warnen, und die Identity-Provider"
        warnung "brauchen eine gültige Adresse. Für den Betrieb ein echtes Zertifikat"
        warnung "angeben und das Skript erneut aufrufen."
    fi

    cat >/etc/nginx/conf.d/zeiterfassung.conf <<EOF
# Erzeugt von install-dnf.sh; ein weiterer Lauf überschreibt diese Datei.
server {
    listen 80;
    listen [::]:80;
    server_name ${ZE_DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${ZE_DOMAIN};

    ssl_certificate     ${zert};
    ssl_certificate_key ${schluessel};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:zeiterfassung:10m;

    # Für Importe.
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:${ZE_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Exporte großer Zeiträume brauchen etwas.
        proxy_read_timeout 120s;
    }
}
EOF
    chmod 644 /etc/nginx/conf.d/zeiterfassung.conf

    if command -v getenforce >/dev/null && [ "$(getenforce)" != "Disabled" ]; then
        # Ohne diesen Schalter verbietet SELinux nginx die Verbindung zum
        # Webdienst ("502 Bad Gateway", im Audit-Log name_connect).
        setsebool -P httpd_can_network_connect 1
        restorecon /etc/nginx/conf.d/zeiterfassung.conf "$zert" "$schluessel" 2>/dev/null || true
        ok "SELinux: httpd_can_network_connect an"
    fi

    nginx -t
    systemctl enable nginx
    systemctl reload-or-restart nginx
    ok "nginx läuft"

    if systemctl is-active --quiet firewalld; then
        firewall-cmd --permanent --add-service=http --add-service=https >/dev/null
        firewall-cmd --reload >/dev/null
        ok "Firewall: http und https offen"
    fi
}

admin_anlegen() {
    schritt "System-Admin"
    local vorgabe=ja
    $NICHT_INTERAKTIV && vorgabe=nein
    if ja_nein "Jetzt ein System-Admin-Konto mit Passwort anlegen (für /admin/)?" "$vorgabe" ZE_ADMIN_ANLEGEN; then
        $NICHT_INTERAKTIV &&
            fehler "createsuperuser braucht ein Terminal; --ja und ZE_ADMIN_ANLEGEN=ja passen nicht zusammen."
        als_dienst podman exec -it zeiterfassung-web python manage.py createsuperuser ||
            warnung "Kein Konto angelegt, das geht auch später (siehe unten)."
    fi
}

abschluss() {
    schritt "Fertig"
    local als="sudo -u $BENUTZER XDG_RUNTIME_DIR=/run/user/$DIENST_UID"
    info "Adresse:        https://$ZE_DOMAIN/"
    if [ -n "$KEYCLOAK_CLIENT_ID" ]; then
        info "Keycloak:       Redirect-URI https://$ZE_DOMAIN/accounts/oidc/keycloak/login/callback/"
    fi
    if [ -n "$ENTRA_CLIENT_ID" ]; then
        info "Entra ID:       Redirect-URI https://$ZE_DOMAIN/accounts/oidc/entra/login/callback/"
    fi
    if ! $NGINX; then
        info "Reverse Proxy:  eigener; HTTPS beenden, an 127.0.0.1:$ZE_PORT weiterleiten"
        info "                und X-Forwarded-Proto setzen"
    fi
    info "Konfiguration:  $ENV_DATEI"
    info "Sicherungen:    $ZE_SICHERUNGEN (jede Nacht um halb drei)"
    printf '\n'
    info "Befehle als Dienstbenutzer, zum Beispiel:"
    info "  $als journalctl --user -u zeiterfassung-web -f"
    info "  $als podman exec -it zeiterfassung-web python manage.py createsuperuser"
    info "Neue Version: im geklonten Repository git pull, dann sudo ./install-dnf.sh."
    printf '\n'
    warnung "Die erzeugten Geheimnisse liegen nur in Podman. Ohne DJANGO_SECRET_KEY und"
    warnung "Datenbankpasswort taugt eine Sicherung nur halb; was zusätzlich gesichert"
    warnung "gehört, steht in docs/podman-quadlet.md unter \"Was die Sicherung nicht enthält\"."
}

main() {
    argumente "$@"
    voraussetzungen
    abfragen
    pakete
    dienstbenutzer
    image_bauen
    geheimnisse
    konfiguration
    datenverzeichnisse
    units
    starten
    nginx_einrichten
    admin_anlegen
    abschluss
}

main "$@"
