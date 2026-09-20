from django.core.management.base import BaseCommand
from django.db import transaction

from apps.groups.models import Activity, Group


class Command(BaseCommand):
    help = "Legt eine Beispielgruppe mit Tätigkeiten an, damit man sofort stempeln kann."

    @transaction.atomic
    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(
            name="Beispielgruppe", defaults={"cost_center": "1000"}
        )
        for order, name in enumerate(["Montage", "Wartung", "Buero", "Schulung"], start=1):
            Activity.objects.get_or_create(
                group=group, name=name, defaults={"sort_order": order * 10}
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Gruppe '{group.name}' {'angelegt' if created else 'vorhanden'}, "
                f"{group.activities.count()} Tätigkeiten."
            )
        )
        self.stdout.write(
            "Nutzer ordnest du im Adminbereich oder über die Mitgliederverwaltung zu."
        )
