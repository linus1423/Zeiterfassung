# Spezifikation: Zeiterfassung

Stand: 18.09.2026 · Entwurf zur Abstimmung
Repository: https://github.com/linus1423/Zeiterfassung

Stand der Umsetzung: das Grundgerüst steht, die Erweiterungen aus Kapitel 13 sind
umgesetzt.

Alles hier ist ein Vorschlag. Was nicht eindeutig aus deiner Beschreibung folgt, steht
in Kapitel 12 als nummerierte Rückfrage mit meiner Empfehlung. Du kannst einfach
antworten "alles wie vorgeschlagen, außer 4 und 11 …".

---

## 1. Ziel

Ein Web-Tool, mit dem Mitarbeitende ihre Arbeitszeit stempeln: Start, Stop und Pausen.
Nutzer sind in Gruppen organisiert. Jede Gruppe hat eigene Tätigkeiten und eigene
Admins. Diese Admins verwalten die Tätigkeiten ihrer Gruppe und bearbeiten Anträge auf
Korrektur bereits erfasster Zeiten. Der Login läuft über OAuth beziehungsweise OpenID
Connect, wahlweise gegen Keycloak oder Microsoft Entra ID.

### Nicht-Ziele (erste Version)

- Keine Urlaubs- oder Krankheitsverwaltung
- Keine Lohnabrechnung und keine Schnittstelle dorthin
- Keine Schicht- oder Dienstplanung
- Keine Projektbudgets oder Kostensätze
- Keine native App (die Weboberfläche ist für das Handy tauglich)

---

## 2. Rollen

| Rolle | Rechte |
|---|---|
| **Mitarbeitende** | Eigene Zeit stempeln, eigene Einträge sehen, Korrektur beantragen |
| **Gruppen-Admin** | Zusätzlich: Tätigkeiten der eigenen Gruppe pflegen, Zeiten aller Gruppenmitglieder sehen, Korrekturanträge der Gruppe entscheiden, Mitglieder der Gruppe verwalten |
| **Buchhaltung** | Lesender Zugriff auf die Zeiten **aller** Gruppen und aller Nutzer, Auswertungen, Export nach Excel und CSV mit frei wählbaren Spalten. Ändert keine Zeiten, entscheidet keine Korrekturanträge, verwaltet keine Tätigkeiten oder Mitglieder |
| **System-Admin** | Zusätzlich: Gruppen anlegen und löschen, Gruppen-Admins und Buchhaltung ernennen, Django-Adminoberfläche |

Die Rolle Gruppen-Admin gilt immer nur für die jeweilige Gruppe. Wer in Gruppe A Admin
ist, ist in Gruppe B normales Mitglied. Ein Gruppen-Admin ist gleichzeitig ganz normales
Mitglied seiner Gruppe und stempelt selbst.

Die Rolle Buchhaltung gilt dagegen **gruppenübergreifend** für die ganze Installation.
Sie ist bewusst rein lesend: Buchhaltung sieht alles, ändert aber nichts. Damit bleibt
die Verantwortung für die Richtigkeit der Zeiten bei den Gruppen-Admins, und die
Buchhaltung kann sich auf Auswertung und Export beschränken. Wer Buchhaltung ist, kann
daneben ganz normal Mitglied einer Gruppe sein und selbst stempeln.

---

## 3. Datenmodell

### 3.1 Übersicht

```
User ──< GroupMembership >── Group ──< Activity
 │                             │
 │                             └──< (Admins = GroupMembership mit role=admin)
 │
 ├──< TimeEntry >── Activity
 │      │
 │      ├──< BreakEntry
 │      └──< CorrectionRequest >── User (Entscheider)
 │
 └──< ExportProfile   (Vorlagen der Buchhaltung)
```

### 3.2 Entitäten

**User** (eigenes Modell, erbt von `AbstractUser`)

| Feld | Typ | Bemerkung |
|---|---|---|
| `email` | E-Mail, eindeutig | zugleich Login-Identität aus dem OIDC-Token |
| `first_name`, `last_name` | Text | kommen aus dem Token |
| `is_active` | bool | gesperrte Nutzer können sich nicht anmelden |
| `is_accounting` | bool | Rolle Buchhaltung, gilt für die ganze Installation |
| `display_name` | Text, optional | falls der Anzeigename vom Namen abweichen soll |

Die Rolle Buchhaltung steht bewusst am Nutzer und nicht in `GroupMembership`, weil sie
nicht an eine Gruppe gebunden ist. Vergeben wird sie von einem System-Admin.

Ein eigenes User-Modell wird von Anfang an angelegt, weil ein späterer Wechsel in
Django sehr aufwendig ist.

**Group** (eigenes Modell, nicht Djangos `auth.Group`)

| Feld | Typ | Bemerkung |
|---|---|---|
| `name` | Text, eindeutig | z. B. "Werkstatt" |
| `slug` | Slug | für URLs |
| `cost_center` | Text, optional | Kostenstelle für den Export |
| `month_start_day` | Zahl 1 bis 28 | Beginn des Abrechnungszeitraums, 1 = Kalendermonat |
| `idp_identifier` | Text, optional | Name der Gruppe im Token des Identity-Providers |
| `is_active` | bool | archivieren statt löschen |
| `created_at` | Zeitpunkt | |

**GroupMembership** (Zuordnung Nutzer ↔ Gruppe)

| Feld | Typ | Bemerkung |
|---|---|---|
| `user`, `group` | FK | eindeutig zusammen |
| `role` | Auswahl | `member` oder `admin` |
| `source` | Auswahl | `manual` (im Tool gepflegt) oder `idp` (aus dem Token) |
| `joined_at` | Zeitpunkt | |

Ein Nutzer kann in mehreren Gruppen sein (siehe Rückfrage 3).

**PeriodLock** (Abschluss eines Abrechnungszeitraums)

| Feld | Typ | Bemerkung |
|---|---|---|
| `group` | FK | ein Abschluss gilt für eine Gruppe |
| `period_start`, `period_end` | Datum | Zeitraum einschließlich, eindeutig je Gruppe |
| `closed_by` | FK, optional | wer abgeschlossen hat |
| `closed_at` | Zeitpunkt | |
| `note` | Text, optional | |

Solange ein Abschluss besteht, nimmt das System für diesen Zeitraum keine Korrekturen
mehr an. Aufheben darf ihn nur ein System-Admin.

**Activity** (Tätigkeit)

| Feld | Typ | Bemerkung |
|---|---|---|
| `group` | FK | Tätigkeiten gehören genau einer Gruppe |
| `name` | Text | eindeutig innerhalb der Gruppe |
| `description` | Text, optional | |
| `is_active` | bool | deaktivierte Tätigkeiten sind nicht mehr wählbar, alte Einträge bleiben gültig |
| `sort_order` | Zahl | Reihenfolge in der Auswahl |

Tätigkeiten werden nie gelöscht, sondern deaktiviert, damit historische Zeiten
auswertbar bleiben.

**TimeEntry** (Zeiteintrag, eine Stempelung von Start bis Stop)

| Feld | Typ | Bemerkung |
|---|---|---|
| `user` | FK | |
| `group` | FK | die Gruppe, für die gestempelt wurde |
| `activity` | FK, optional | siehe Rückfrage 5 |
| `start` | Zeitpunkt | |
| `end` | Zeitpunkt, leer solange laufend | |
| `note` | Text, optional | |
| `source` | Auswahl | `clock` (gestempelt), `correction` (nachträglich geändert), `import` |
| `created_at`, `updated_at` | Zeitpunkt | |

Abgeleitet, nicht gespeichert: `duration` = `end` − `start` − Summe der Pausen.

Regeln:
- Pro Nutzer darf höchstens ein Eintrag ohne `end` existieren (Datenbank-Constraint).
- `end` muss nach `start` liegen.
- Überlappende Einträge desselben Nutzers sind nicht erlaubt.

**BreakEntry** (Pause)

| Feld | Typ | Bemerkung |
|---|---|---|
| `time_entry` | FK | Pause gehört zu einem Zeiteintrag |
| `start`, `end` | Zeitpunkt | `end` leer solange die Pause läuft |
| `is_automatic` | bool | markiert Pausen, die das System nach Gesetz ergänzt hat (Rückfrage 7) |

Regeln: Pausen liegen innerhalb ihres Zeiteintrags, überlappen sich nicht, und pro
Zeiteintrag ist höchstens eine Pause offen.

**CorrectionRequest** (Korrekturantrag)

| Feld | Typ | Bemerkung |
|---|---|---|
| `time_entry` | FK, optional | leer, wenn ein vergessener Eintrag nachgetragen wird |
| `requested_by` | FK User | |
| `group` | FK | bestimmt, welche Admins zuständig sind |
| `kind` | Auswahl | `edit`, `create`, `delete` |
| `proposed_start`, `proposed_end` | Zeitpunkt | gewünschter Zustand |
| `proposed_activity` | FK, optional | |
| `proposed_breaks` | JSON | Liste gewünschter Pausen |
| `reason` | Text, Pflicht | Begründung des Antragstellers |
| `status` | Auswahl | `pending`, `approved`, `rejected`, `withdrawn` |
| `decided_by` | FK User, optional | |
| `decided_at` | Zeitpunkt, optional | |
| `decision_note` | Text, optional | Begründung der Ablehnung |

Beim Genehmigen schreibt das System die vorgeschlagenen Werte in den Zeiteintrag und
setzt dessen `source` auf `correction`. Der Antrag bleibt als Beleg erhalten.

**ExportProfile** (gespeicherte Export-Vorlage)

| Feld | Typ | Bemerkung |
|---|---|---|
| `name` | Text | z. B. "Monatsabrechnung Lohnbüro" |
| `owner` | FK User | wem die Vorlage gehört |
| `is_shared` | bool | auch für andere Buchhaltungsnutzer sichtbar |
| `columns` | JSON | Liste der Spalten in der gewünschten Reihenfolge |
| `grouping` | Auswahl | `entry` (eine Zeile je Zeiteintrag), `user_day`, `user_month`, `activity` |
| `filters` | JSON | Vorbelegung für Zeitraum, Gruppen, Nutzer, Tätigkeiten |
| `filters.csv_dialect` | Text | Trennzeichen-Variante fuer CSV |
| `created_at`, `updated_at` | Zeitpunkt | |

Damit muss die Buchhaltung ihre Spaltenauswahl nicht jeden Monat neu zusammenklicken.

**AuditLog** (Änderungsprotokoll)

Wer hat wann welchen Zeiteintrag wie geändert. Enthält Nutzer, Zeitpunkt, Objekt,
alte und neue Werte. Nur lesbar, nie änderbar. Notwendig, weil Arbeitszeit
nachvollziehbar sein muss.

---

## 4. Stempel-Logik

Der Zustand eines Nutzers ist immer genau einer von dreien:

1. **Ausgestempelt** – kein offener Zeiteintrag
2. **Arbeitet** – offener Zeiteintrag, keine offene Pause
3. **In Pause** – offener Zeiteintrag mit offener Pause

Erlaubte Übergänge:

| Von | Aktion | Nach |
|---|---|---|
| Ausgestempelt | Start (Gruppe und Tätigkeit wählen) | Arbeitet |
| Arbeitet | Pause beginnen | In Pause |
| In Pause | Pause beenden | Arbeitet |
| Arbeitet | Stop | Ausgestempelt |
| In Pause | Stop | Ausgestempelt (Pause wird mitbeendet) |

Alles andere wird abgewiesen, auch wenn zwei Geräte gleichzeitig senden. Das wird über
eine Transaktion mit Sperre auf dem offenen Eintrag abgesichert, damit ein doppelter
Klick nicht zwei Einträge erzeugt.

### Grenzfälle

- **Vergessenes Ausstempeln:** Ein Eintrag, der über eine konfigurierte Höchstdauer
  hinausläuft (Vorschlag 16 Stunden), wird automatisch beendet und als "unvollständig"
  markiert. Der Nutzer sieht beim nächsten Login einen Hinweis und kann direkt einen
  Korrekturantrag stellen. Siehe Rückfrage 6.
- **Mitternacht:** Ein Eintrag darf über Mitternacht laufen. Für die Tagesauswertung
  wird er am Tagesgrenzpunkt anteilig aufgeteilt, in der Datenbank aber nicht zerlegt.
- **Zeitzonen und Sommerzeit:** Alle Zeitpunkte werden in UTC gespeichert
  (`USE_TZ = True`) und in der Anzeigezeitzone dargestellt. Dadurch bleibt die
  Zeitumstellung korrekt.
- **Zukunft:** Stempeln in der Zukunft ist nicht möglich, die Zeit kommt immer vom
  Server, nie vom Browser.

---

## 5. Korrektur-Workflow

1. Nutzer öffnet einen eigenen Eintrag und beantragt eine Änderung, oder trägt einen
   vergessenen Eintrag nach. Begründung ist Pflicht.
2. Der Antrag landet bei den Admins der zugehörigen Gruppe.
3. Ein Admin genehmigt oder lehnt ab, bei Ablehnung mit Begründung.
4. Bei Genehmigung ändert das System den Eintrag und schreibt ins Änderungsprotokoll.
5. Der Antragsteller wird benachrichtigt (siehe Rückfrage 9).

Der Nutzer ändert seine Zeiten nie direkt. Ein Admin kann Zeiten seiner Gruppe direkt
ändern und fehlende nachtragen, das wird aber ebenfalls protokolliert; ein fast
richtiger Antrag lässt sich auch mit Änderung genehmigen (siehe Kapitel 14). Ein
Admin darf seinen eigenen Antrag nicht selbst entscheiden (siehe Rückfrage 10).

---

## 6. Anmeldung mit OAuth / OpenID Connect

- Umsetzung mit **django-allauth**, Provider über OpenID Connect.
- Keycloak und Entra ID sind beide OIDC, laufen also über denselben Mechanismus, nur
  mit unterschiedlicher Konfiguration (Discovery-URL, Client-ID, Client-Secret).
- Beide Provider können gleichzeitig aktiv sein. Auf der Login-Seite erscheint pro
  konfiguriertem Provider ein Knopf. Ist nur einer konfiguriert, wird direkt
  dorthin weitergeleitet.
- Zugangsdaten kommen ausschließlich aus Umgebungsvariablen, nie aus dem Repository.
- Lokale Passwörter sind abgeschaltet, einzige Ausnahme ist der System-Admin für den
  Notfallzugang (siehe Rückfrage 12).
- Beim ersten Login wird der Nutzer angelegt, Name und E-Mail kommen aus dem Token.
- Verknüpfung bestehender Nutzer über die E-Mail-Adresse, damit derselbe Mensch nicht
  über zwei Provider zwei Konten bekommt.
- Abmelden beendet die Django-Sitzung. Ob zusätzlich beim Provider abgemeldet wird,
  ist Rückfrage 13.

Die Zuordnung zu Gruppen erfolgt im Standardvorschlag **in der Anwendung**, nicht über
den Identity-Provider: ein Admin ordnet Nutzer Gruppen zu. Alternativ ließe sich das aus
Token-Claims ableiten, siehe Rückfrage 2.

---

## 7. Oberfläche

**Stempeluhr (Startseite)**
Großer Zustand ("Du arbeitest seit 07:12"), je nach Zustand die Knöpfe Start, Pause,
Weiter, Stop. Bei Start Auswahl von Gruppe (falls mehrere) und Tätigkeit. Darunter die
heutigen Einträge mit Summe. Auf dem Handy bedienbar, große Flächen, wenige Klicks.

**Meine Zeiten**
Liste nach Woche oder Monat, mit Tagessummen und Wochensumme, Filter nach Tätigkeit,
je Eintrag ein Knopf "Korrektur beantragen".

**Gruppenübersicht (nur Admins)**
Wer ist gerade eingestempelt, Zeiten aller Mitglieder im gewählten Zeitraum, Summen je
Mitarbeitendem und je Tätigkeit.

**Tätigkeiten verwalten (nur Admins)**
Anlegen, umbenennen, Reihenfolge, deaktivieren.

**Korrekturanträge (nur Admins)**
Liste der offenen Anträge mit Gegenüberstellung alt/neu, Genehmigen oder Ablehnen.

**Mitglieder verwalten (nur Admins)**
Nutzer zur Gruppe hinzufügen oder entfernen, Admin-Rolle vergeben.

**Auswertung und Export (nur Buchhaltung)**
Eine Seite über alle Gruppen hinweg: Zeitraum wählen, nach Gruppe, Nutzer und Tätigkeit
filtern, Verdichtung wählen (je Eintrag, je Nutzer und Tag, je Nutzer und Monat, je
Tätigkeit). Die Spalten werden per Ankreuzliste zusammengestellt und per Ziehen
sortiert. Die Tabelle zeigt eine Vorschau der ersten Zeilen, darunter liegen die Knöpfe
"Als Excel herunterladen" und "Als CSV herunterladen". Die aktuelle Zusammenstellung
lässt sich als Vorlage speichern und beim nächsten Mal mit einem Klick laden.

---

## 8. Auswertung und Export

Gruppen-Admins werten ihre eigene Gruppe aus. Die Buchhaltung wertet alle Gruppen aus
und ist die einzige Rolle mit dem vollen Export.

### Verdichtung

Vor dem Export wird gewählt, was eine Zeile bedeutet:

| Verdichtung | Eine Zeile ist |
|---|---|
| `entry` | ein einzelner Zeiteintrag (Rohdaten) |
| `user_day` | ein Nutzer an einem Tag |
| `user_month` | ein Nutzer in einem Abrechnungszeitraum (je Gruppen-Zyklus) |
| `activity` | eine Tätigkeit im gewählten Zeitraum |

### Wählbare Spalten

Die Buchhaltung stellt sich die Spalten selbst zusammen und bringt sie in die gewünschte
Reihenfolge. Verfügbar sind:

| Spalte | Inhalt |
|---|---|
| Personalnummer | freies Feld am Nutzer, für die Lohnbuchhaltung |
| Nachname, Vorname, Anzeigename | Name des Nutzers |
| E-Mail | Login-Adresse |
| Gruppe | Name der Gruppe |
| Tätigkeit | Name der Tätigkeit |
| Datum | Tag des Beginns |
| Wochentag | Montag bis Sonntag |
| Kalenderwoche | ISO-Woche |
| Monat, Jahr | für Monatsabrechnungen |
| Beginn, Ende | Uhrzeit in der Anzeigezeitzone |
| Pausendauer | Summe der Pausen |
| Arbeitszeit (h) | Dauer als Dezimalzahl, z. B. 7,75 |
| Arbeitszeit (hh:mm) | Dauer als Zeitangabe |
| Anzahl Einträge | bei verdichteten Zeilen |
| Erfassungsart | gestempelt, korrigiert, importiert |
| Unvollständig | Kennzeichen für automatisch beendete Einträge |
| Notiz | freier Text am Eintrag |
| Zuletzt geändert am / von | für die Nachvollziehbarkeit |

Die Spaltenauswahl, die Filter und die Verdichtung lassen sich als **Export-Vorlage**
speichern (Modell `ExportProfile`) und mit anderen Buchhaltungsnutzern teilen.

### Formate

- **Excel (.xlsx)** mit `openpyxl`: erste Zeile als Überschrift und fixiert, Spalten auf
  Inhaltsbreite, Datums-, Uhrzeit- und Dauerspalten als echte Excel-Formate, damit in
  Excel gerechnet werden kann, und eine optionale Summenzeile.
- **CSV**: Trennzeichen, Dezimaltrennzeichen und Zeichensatz einstellbar. Voreinstellung
  ist die deutsche Variante (Semikolon, Komma als Dezimaltrennzeichen, UTF-8 mit BOM),
  weil Excel Dateien sonst falsch einliest.

Große Exporte werden zeilenweise gestreamt, damit auch ein Jahresexport nicht den
Arbeitsspeicher sprengt.

### Sonstiges

- Summen je Nutzer, Gruppe, Tätigkeit und Zeitraum in der Oberfläche
- Jeder Export wird protokolliert: wer hat wann welchen Zeitraum exportiert
- Keine PDF-Berichte in Version 1 (siehe Rückfrage 21)

---

## 9. Technik

| Thema | Vorschlag |
|---|---|
| Framework | Django, aktuelle LTS-Version |
| Datenbank | PostgreSQL in Produktion, SQLite für lokale Entwicklung |
| Oberfläche | Django-Templates mit HTMX für die Stempel-Knöpfe, kein eigenes Frontend-Framework |
| Gestaltung | Tailwind oder Bootstrap, siehe Rückfrage 17 |
| Anmeldung | django-allauth mit OIDC |
| Konfiguration | Umgebungsvariablen über `django-environ` |
| Tests | pytest mit pytest-django, Schwerpunkt auf Stempel-Logik und Rechten |
| Qualität | ruff für Format und Linting |
| CI | GitHub Actions: Tests, Linting, Migrations-Check bei jedem Push |
| Betrieb | Docker-Compose mit App, PostgreSQL und Reverse Proxy, siehe Rückfrage 16 |
| Hintergrundjobs | Für das automatische Beenden vergessener Einträge, siehe Rückfrage 6 |
| Sprache | Oberfläche auf Deutsch, Code und Kommentare auf Englisch |

### Projektstruktur

```
zeiterfassung/          Projekt-Einstellungen
apps/
  accounts/             User, OIDC-Anbindung
  groups/               Group, GroupMembership, Activity
  tracking/             TimeEntry, BreakEntry, Stempel-Logik
  corrections/          CorrectionRequest, Workflow
  reporting/            Auswertung, Export
templates/
static/
tests/
```

---

## 10. Umsetzung in Schritten

1. Projekt-Gerüst, Einstellungen, User-Modell, CI
2. Datenmodell mit Migrationen und Tests
3. OIDC-Anmeldung für beide Provider
4. Stempeluhr mit Start, Pause, Stop
5. Eigene Zeiten und Gruppenübersicht
6. Tätigkeiten- und Mitgliederverwaltung
7. Korrekturanträge
8. Auswertung, Rolle Buchhaltung, Export nach Excel und CSV mit wählbaren Spalten
9. Docker und Deployment-Anleitung

Jeder Schritt ist ein eigener Pull Request.

---

## 11. Sicherheit und Datenschutz

- Arbeitszeitdaten sind personenbezogen. Zugriff nur auf eigene Daten, Admins nur auf
  ihre Gruppe.
- Die Buchhaltung sieht alle Gruppen, aber nur lesend. Diese Rolle vergibt ausschließlich
  ein System-Admin, und jeder Export wird protokolliert.
- Änderungsprotokoll für alle Änderungen an Zeiten.
- Löschkonzept: Aufbewahrungsfrist und anschließende Anonymisierung, siehe Rückfrage 18.
- Kein Zugriff auf Standortdaten, keine Überwachung, keine Screenshots.

---

## 12. Offene Punkte

Zu jedem Punkt steht meine Empfehlung dabei. Antworte gern nur mit den Nummern, bei
denen du es anders willst.

**Organisation und Gruppen**

1. **Wie viele Nutzer und Gruppen sind realistisch?** Zehn Personen oder tausend?
   *Empfehlung: ich plane für einige hundert Nutzer, das ändert nichts Grundsätzliches,
   aber es entscheidet über den Aufwand bei Auswertungen.*
2. **Kommen die Gruppen aus dem Identity-Provider oder werden sie im Tool gepflegt?**
   Keycloak und Entra können Gruppen im Token mitliefern.
   *Empfehlung: im Tool pflegen. Das ist unabhängig vom Provider und funktioniert für
   beide gleich. Claim-Mapping können wir später ergänzen.*
3. **Kann ein Nutzer in mehreren Gruppen sein?**
   *Empfehlung: ja, das Modell kann es. Beim Stempeln wählt er dann die Gruppe.*
4. **Darf ein Nutzer in mehreren Gruppen gleichzeitig eingestempelt sein?**
   *Empfehlung: nein. Ein Mensch arbeitet zu einem Zeitpunkt an einer Sache.*

**Stempeln**

5. **Ist die Tätigkeit beim Einstempeln Pflicht?**
   *Empfehlung: ja, Pflicht. Sonst sind die Auswertungen später lückenhaft.*
6. **Was passiert bei vergessenem Ausstempeln?**
   *Empfehlung: nach 16 Stunden automatisch beenden, als unvollständig markieren, der
   Nutzer korrigiert per Antrag. Alternative: offen lassen und nur warnen.*
7. **Sollen gesetzliche Pausen automatisch berücksichtigt werden?** In Deutschland
   30 Minuten ab 6 Stunden, 45 ab 9 Stunden.
   *Empfehlung: für Version 1 nur warnen, nicht automatisch abziehen. Automatischer
   Abzug ist arbeitsrechtlich heikel.*
8. **Soll die Tätigkeit während einer laufenden Stempelung wechselbar sein?**
   *Empfehlung: ja, als Stop und sofortiger Neustart mit der neuen Tätigkeit.*

**Korrekturen**

9. **Sollen Nutzer bei Entscheidungen über ihre Anträge benachrichtigt werden, und
   wenn ja, wie?** E-Mail oder nur im Tool?
   *Empfehlung: erst nur im Tool, E-Mail später. Dann brauchen wir jetzt keinen
   Mailserver.*
10. **Darf ein Gruppen-Admin seinen eigenen Antrag entscheiden?**
    *Empfehlung: nein, ein anderer Admin muss es tun. Falls eine Gruppe nur einen Admin
    hat, entscheidet ein System-Admin.*
11. **Gibt es eine Frist, bis wann Zeiten korrigiert werden können?** Zum Beispiel bis
    zum Monatsabschluss.
    *Empfehlung: für Version 1 keine Frist, später ein Monatsabschluss, der Zeiträume
    sperrt.*

**Anmeldung**

12. **Soll es einen lokalen Notfallzugang geben (Passwort, nur für System-Admins)?**
    *Empfehlung: ja. Wenn der Identity-Provider ausfällt, kommt man sonst nicht mehr rein.*
13. **Was soll beim Abmelden passieren?** Nur aus dem Tool oder auch beim Provider?
    *Empfehlung: nur aus dem Tool. Zentrales Abmelden kann man ergänzen.*
14. **Was passiert mit einem Nutzer, der sich anmeldet, aber in keiner Gruppe ist?**
    *Empfehlung: er wird angelegt, sieht aber nur einen Hinweis, dass ein Admin ihn einer
    Gruppe zuordnen muss.*
15. **Hast du schon eine Keycloak-Instanz oder einen Entra-Mandanten zum Testen?**
    *Ich richte die Konfiguration so ein, dass beides über Umgebungsvariablen läuft, und
    schreibe eine Anleitung. Zum echten Testen bräuchte ich Zugangsdaten, die gehören
    aber nicht in den Chat und nicht ins Repository.*

**Technik und Betrieb**

16. **Wo soll das laufen?** Eigener Server, Docker, ein Hoster?
    *Empfehlung: Docker-Compose mit PostgreSQL, das läuft überall.*
17. **Welche Gestaltung?** Tailwind oder Bootstrap?
    *Empfehlung: Tailwind, weil die Stempeloberfläche auf dem Handy gut aussehen soll.*
18. **Gibt es Vorgaben zur Aufbewahrung der Daten?** Arbeitszeitnachweise müssen in
    Deutschland typischerweise zwei Jahre aufbewahrt werden.
    *Empfehlung: Daten unbegrenzt behalten, Anonymisierung ausgeschiedener Mitarbeitender
    als spätere Funktion.*
19. **Braucht es eine API für andere Systeme?**
    *Empfehlung: nein, in Version 1 nur die Weboberfläche und CSV-Export.*
20. **Ist die Oberfläche nur auf Deutsch oder auch auf Englisch nötig?**
    *Empfehlung: nur Deutsch, aber mit Djangos Übersetzungsmechanismus, damit Englisch
    später ohne Umbau geht.*

**Buchhaltung und Export**

21. **Braucht die Buchhaltung PDF-Berichte oder reichen Excel und CSV?**
    *Empfehlung: Excel und CSV reichen. PDF können wir später ergänzen.*
22. **Soll die Buchhaltung wirklich rein lesend sein, oder darf sie Zeiten korrigieren?**
    *Empfehlung: rein lesend. Korrekturen bleiben bei den Gruppen-Admins, sonst wird
    unklar, wer verantwortlich ist.*
23. **Fehlt eine Spalte in der Liste oben?** Typisch wären Personalnummer, Kostenstelle
    oder ein Lohnartenschlüssel.
    *Empfehlung: Personalnummer ist eingeplant, Kostenstelle ergänze ich gern, wenn sie
    gebraucht wird. Beides sind freie Felder am Nutzer beziehungsweise an der Gruppe.*
24. **Gibt es ein festes Format, das ein Lohnprogramm erwartet?** Dann bauen wir es als
    fertige Vorlage ein.
    *Empfehlung: sag mir, welches Programm, dann lege ich die passende Vorlage an.*
25. **Darf die Buchhaltung auch Namen sehen, oder soll es eine anonymisierte Ansicht
    geben?**
    *Empfehlung: Namen sehen, das ist für eine Abrechnung nötig.*

---

## 13. Umgesetzte Erweiterungen (18.09.2026)

Diese Punkte lagen als Issues im Repository und sind jetzt umgesetzt. Wo eine Rückfrage
aus Kapitel 12 dahinter steht, gilt die dort genannte Empfehlung weiter als Vorgabe,
ist aber nun konfigurierbar oder ausgebaut.

**Mehrere Pausen je Korrekturantrag** (Issue 2). Das Antragsformular hat ein Formular
je Pause, höchstens sechs. Die Pausen müssen innerhalb der beantragten Arbeitszeit
liegen und dürfen sich nicht überschneiden; leere Zeilen werden verworfen. Die
Entscheidungsansicht zeigt jede beantragte Pause einzeln mit Summe.

**Benachrichtigungen** (Issue 3, Rückfrage 9). Ohne Mailserver zeigt die Navigation
Zähler: offene Anträge für Admins, neu entschiedene eigene Anträge für Antragstellende.
Der Zähler geht aus, sobald der Antragsteller seine Anträge ansieht. Mit
`CORRECTION_EMAILS_ENABLED=true` kommt zusätzlich eine Mail an die Admins bei einem
neuen Antrag und an den Antragsteller bei der Entscheidung, samt Begründung. Versandt
wird nach dem Commit; ein nicht erreichbarer Mailserver verhindert den Vorgang nicht.

**Gruppen aus den Claims des Identity-Providers** (Issue 4, Rückfrage 2). Aus, solange
`OIDC_GROUP_SYNC` nicht gesetzt ist. Eingeschaltet liest das Tool beim Login den Claim
aus `OIDC_GROUPS_CLAIM` und vergleicht die Werte mit Kurzname, Name und
`idp_identifier` der Gruppe; bei verschachtelten Keycloak-Gruppen passt auch das letzte
Pfadstück. Im Tool gepflegte Mitgliedschaften bleiben unangetastet, der Provider legt
eigene an (`source=idp`) und ändert auch nur diese. `OIDC_GROUP_SYNC_MODE=replace`
entzieht diese wieder, `add` ergänzt nur. Die Admin-Rolle kommt nur aus dem Token, wenn
`OIDC_ADMIN_GROUPS_CLAIM` oder `OIDC_ADMIN_GROUP_SUFFIX` gesetzt ist; der letzte Admin
einer Gruppe verliert die Rolle nie automatisch.

**Monatsabschluss** (Issue 5, Rückfrage 11). Ein Admin der Gruppe schließt einen
abgelaufenen Abrechnungszeitraum ab. Danach lehnt das System neue Korrekturanträge für
diesen Zeitraum ab, und auch schon gestellte Anträge lassen sich nicht mehr genehmigen.
Der laufende Zeitraum kann nicht abgeschlossen werden. Wieder öffnen darf nur ein
System-Admin; beides steht im Protokoll. Die Auswertung zeigt je Gruppe den letzten
Abschluss und je Zeile, ob sie in einem abgeschlossenen Zeitraum liegt.

**Aufbewahrung und Anonymisierung** (Issue 6, Rückfrage 18). Frist:
`DATA_RETENTION_MONTHS`, Vorgabe 24 Monate. Das Kommando
`anonymize_expired_users` zeigt betroffene Konten an und anonymisiert sie erst mit
`--apply`: Name, E-Mail und Personalnummer werden ersetzt, das Konto deaktiviert, die
Verknüpfung zum Identity-Provider und die Mitgliedschaften entfernt. Die Zeiteinträge
bleiben für die Statistik erhalten, sind aber keiner Person mehr zuzuordnen. Angefasst
werden nur Konten ohne Zeiten, ohne Anmeldung und ohne offenen Antrag innerhalb der
Frist; neue Konten bleiben also unberührt.

**Vergessene Stempelungen planmäßig beenden** (Issue 7). Das Compose-Setup hat einen
Dienst `scheduler`, der `close_stale_entries` in einer Schleife aufruft, standardmäßig
jede Stunde (`SCHEDULER_INTERVAL_SECONDS`). Ohne Compose übernimmt das ein Cronjob oder
ein systemd-Timer.

**Eigener Abrechnungszeitraum je Gruppe** (Issue 8). `month_start_day` der Gruppe legt
fest, an welchem Tag ein Zeitraum beginnt, höchstens am 28., damit es den Tag in jedem
Monat gibt. 1 bedeutet Kalendermonat. Der Zyklus gilt für die Vorbelegung der
Gruppenansicht, den Abschluss und die Verdichtung "Je Nutzer und Monat": zwei Tage im
selben Kalendermonat können in verschiedenen Abrechnungszeiträumen liegen und werden
dann getrennt ausgewiesen. Der Export hat dafür die Spalten Abrechnungszeitraum,
Zeitraum von, Zeitraum bis und Abgeschlossen.

---

## 14. Umgesetzte Erweiterungen (20.09.2026)

Diese Punkte kamen aus dem Durchgang über die Funktionsweise und lagen als
Issues 25 bis 30 im Repository.

**Umlaute in der Oberfläche** (Issue 25). Alle für Nutzer sichtbaren Texte
stehen jetzt in korrektem Deutsch: Templates, Formularbeschriftungen,
Meldungen, Feldbezeichnungen und Hilfetexte. Bezeichner im Code, URL-Pfade und
die Werte der Auswahlfelder in der Datenbank sind unverändert geblieben, damit
sich weder Adressen noch gespeicherte Daten ändern.

**Notiz sichtbar** (Issue 26). Die beim Einstempeln erfasste Notiz steht jetzt
in der Tagesliste der Stempeluhr, in "Meine Zeiten" und in der Eintragsliste
der Gruppenübersicht. Bisher war sie nur als Exportspalte zu sehen.

**Auswahl beim Einstempeln** (Issue 27). Gehört jemand nur einer Gruppe an,
entfällt das Gruppenfeld. Bei mehreren Gruppen sind die Tätigkeiten in der
Auswahl nach Gruppe gebündelt, sodass zwei gleichnamige Tätigkeiten
unterscheidbar sind. Gruppe und Tätigkeit des letzten Eintrags sind
vorbelegt, solange es sie noch gibt und sie aktiv sind.

**Meine Zeiten** (Issue 28). Zusätzlich zu den Tagessummen gibt es
Wochensummen, einen Filter nach Tätigkeit und Schnellschalter für diese Woche,
vorige Woche, diesen und vorigen Abrechnungszeitraum. Der Vorgabezeitraum folgt
dem Abrechnungszyklus der Gruppe; gehört jemand zu Gruppen mit
unterschiedlichen Zyklen, bleibt es beim Kalendermonat. Die eigenen Zeiten
lassen sich als Excel oder CSV herunterladen, mit fester Spaltenliste: die frei
wählbare Zusammenstellung bleibt der Auswertung vorbehalten.

**Nutzerstammdaten** (Issue 29). System-Admins pflegen unter "Nutzer" die
Personalnummer und die Rolle Buchhaltung, mit Suche über Name, E-Mail und
Personalnummer. Die Personalnummer ist eindeutig; Name und E-Mail kommen
weiterhin aus dem Identity-Provider und sind nicht änderbar. Jede Änderung
steht im Protokoll (`user_updated`). Die Mitgliederliste einer Gruppe zeigt die
Personalnummer mit, damit ein Gruppen-Admin sieht, wo sie fehlt.

**Tätigkeit wechseln** (Issue 30, Rückfrage 8). Im Zustand "arbeitet" gibt es
auf der Stempeluhr die Auswahl "Tätigkeit wechseln". Der laufende Eintrag wird
beendet und im selben Moment ein neuer mit der neuen Tätigkeit begonnen, ohne
Lücke dazwischen. Die Gruppe bleibt dabei dieselbe, ein Wechsel der Gruppe
läuft weiter über Stop und Start. Während einer Pause ist der Wechsel nicht
möglich; beides steht als Aus- und Einstempeln im Protokoll.

**Zeiten direkt ändern** (Issue 31). Ein Gruppen-Admin ändert Zeiten seiner
Gruppe jetzt auch direkt, ohne den Umweg über einen Antrag: in der
Gruppenübersicht führt bei jedem abgeschlossenen Eintrag "Ändern" zum
Formular, und "Zeit nachtragen" legt eine fehlende Zeit für ein Mitglied an,
etwa wenn jemand krank ist. Eine Begründung ist Pflicht. In der
Entscheidungsansicht eines Antrags gibt es zusätzlich "Genehmigen mit
Änderung": der Admin passt Beginn, Ende, Tätigkeit und Pausen an und
übernimmt sie, statt einen fast richtigen Antrag abzulehnen. Der Antrag
selbst bleibt als Beleg unverändert; was tatsächlich übernommen wurde, steht
daneben und ist für den Antragsteller in seiner Antragsliste sichtbar.

Es gelten dabei dieselben Regeln wie beim genehmigten Antrag: ein
abgeschlossener Zeitraum sperrt, Zeiten desselben Nutzers dürfen sich nicht
überschneiden, ein laufender Eintrag lässt sich nicht ändern, und jede
Änderung steht mit vorher und nachher im Protokoll (`entry_updated`). Die
geänderte Zeit gilt danach als korrigiert (`source = correction`) und nicht
mehr als unvollständig. Die Buchhaltung liest weiterhin nur und ändert nichts
(Rückfrage 22).
