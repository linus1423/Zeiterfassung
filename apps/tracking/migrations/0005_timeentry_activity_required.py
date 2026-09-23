"""Die Tätigkeit eines Zeiteintrags wird Pflicht (Issue 83).

Bestandsdaten ohne Tätigkeit bekommen je Gruppe eine Sammel-Tätigkeit
"Ohne Tätigkeit". Sie wird deaktiviert angelegt, damit sie in keiner Auswahl
auftaucht und nur die alten Einträge an ihr hängen.
"""

import django.db.models.deletion
from django.db import migrations, models

SAMMEL_NAME = "Ohne Tätigkeit"


def sammel_taetigkeit_setzen(apps, schema_editor):
    TimeEntry = apps.get_model("tracking", "TimeEntry")
    Activity = apps.get_model("groups", "Activity")

    gruppen = (
        TimeEntry.objects.filter(activity__isnull=True)
        .values_list("group_id", flat=True)
        .distinct()
    )
    for group_id in list(gruppen):
        sammel, _ = Activity.objects.get_or_create(
            group_id=group_id,
            name=SAMMEL_NAME,
            defaults={
                "description": "Automatisch angelegt für Zeiten, die vor Issue 83 "
                "ohne Tätigkeit erfasst wurden.",
                "is_active": False,
                "sort_order": 999,
            },
        )
        TimeEntry.objects.filter(group_id=group_id, activity__isnull=True).update(
            activity_id=sammel.pk
        )


class Migration(migrations.Migration):
    dependencies = [
        ("groups", "0004_periodconfirmation"),
        ("tracking", "0004_entryimport"),
    ]

    operations = [
        migrations.RunPython(sammel_taetigkeit_setzen, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="timeentry",
            name="activity",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="time_entries",
                to="groups.activity",
                verbose_name="Tätigkeit",
            ),
        ),
    ]
