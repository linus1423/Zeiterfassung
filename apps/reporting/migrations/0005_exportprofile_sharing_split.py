from django.db import migrations, models


class Migration(migrations.Migration):
    """Aus `is_shared` werden zwei getrennte Schalter (Issue 72).

    Der Bestand wandert nach `share_with_accounting`: so war der Haken
    beschriftet, das war also die Absicht beim Setzen. Ob er bisher gewirkt
    hat, hing am Besitzer; daran soll sich für die Buchhaltung nichts ändern.
    """

    dependencies = [
        ("reporting", "0004_exportschedule_exportrun_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="exportprofile",
            old_name="is_shared",
            new_name="share_with_accounting",
        ),
        migrations.AlterField(
            model_name="exportprofile",
            name="share_with_accounting",
            field=models.BooleanField(
                default=False,
                help_text="Sichtbar für alle Nutzer mit der Rolle Buchhaltung.",
                verbose_name="Mit der Buchhaltung teilen",
            ),
        ),
        migrations.AddField(
            model_name="exportprofile",
            name="share_with_group_admins",
            field=models.BooleanField(
                default=False,
                help_text="Sichtbar für alle Admins der Gruppen, die der Besitzer selbst verwaltet.",
                verbose_name="Mit den Admins meiner Gruppen teilen",
            ),
        ),
    ]
