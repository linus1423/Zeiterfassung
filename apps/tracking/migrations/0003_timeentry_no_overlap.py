"""Überschneidungsfreiheit in der Datenbank erzwingen (Issue 43).

Bisher stand die Regel "die Zeiten einer Person dürfen sich nicht
überschneiden" nur in der Anwendung. Sie liest dafür, und zwischen Lesen und
Schreiben kann ein anderer Vorgang dieselbe Lücke belegen. Eine
Ausschlussbedingung in der Datenbank gilt unabhängig vom Weg und auch dann,
wenn zwei Vorgänge gleichzeitig schreiben.

Nur PostgreSQL kennt Ausschlussbedingungen. Unter SQLite, das in den Tests
läuft, macht diese Migration nichts; dort bleibt es bei der Prüfung in der
Anwendung.
"""

from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations

TABLE = "tracking_timeentry"
CONSTRAINT_NAME = "time_entry_no_overlap_per_user"

# Halboffenes Intervall: ein Eintrag, der um 12:00 endet, und einer, der um
# 12:00 beginnt, überschneiden sich nicht (so wechselt switch_activity die
# Tätigkeit). Ein laufender Eintrag hat kein Ende und reicht damit bis
# unendlich, genau wie die Prüfung in der Anwendung ihn behandelt.
RANGE = 'tstzrange("start", "end")'

ADD_CONSTRAINT = f"""
ALTER TABLE {TABLE}
ADD CONSTRAINT {CONSTRAINT_NAME}
EXCLUDE USING gist (user_id WITH =, {RANGE} WITH &&)
"""

DROP_CONSTRAINT = f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"

# Bestehende Daten können die Bedingung verletzen, etwa aus einem Import oder
# aus der Zeit vor dieser Migration. Dann bricht das ALTER TABLE mit einer
# Meldung ab, die nur den ersten Konflikt nennt; deshalb wird vorher gesucht
# und verständlich berichtet.
FIND_OVERLAPS = f"""
SELECT a.id, b.id
FROM {TABLE} a
JOIN {TABLE} b ON b.user_id = a.user_id AND b.id > a.id
WHERE tstzrange(a."start", a."end") && tstzrange(b."start", b."end")
ORDER BY a.id, b.id
LIMIT 10
"""


def add_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FIND_OVERLAPS)
        clashes = cursor.fetchall()
    if clashes:
        paare = ", ".join(f"{first} und {second}" for first, second in clashes)
        raise RuntimeError(
            "Es gibt bereits Zeiteinträge, die sich überschneiden; die Bedingung "
            "kann deshalb nicht angelegt werden. Betroffen sind die Zeiteinträge "
            f"{paare} (höchstens die ersten zehn Paare). Bitte diese Zeiten "
            "bereinigen und die Migration erneut starten."
        )

    schema_editor.execute(ADD_CONSTRAINT)


def drop_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_CONSTRAINT)


class Migration(migrations.Migration):
    dependencies = [
        ("tracking", "0002_alter_timeentry_options_alter_timeentry_activity_and_more"),
    ]

    operations = [
        # Legt die Erweiterung nur unter PostgreSQL an und braucht dafür ein
        # Konto mit den nötigen Rechten (üblicherweise der Eigentümer der
        # Datenbank oder ein Superuser).
        BtreeGistExtension(),
        migrations.RunPython(add_constraint, drop_constraint),
    ]
