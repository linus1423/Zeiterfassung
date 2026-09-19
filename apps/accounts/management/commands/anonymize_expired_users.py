from django.core.management.base import BaseCommand

from apps.accounts import retention


class Command(BaseCommand):
    help = (
        "Anonymisiert Konten, deren Aufbewahrungsfrist abgelaufen ist. "
        "Zeigt ohne --apply nur an, was passieren wuerde."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--months",
            type=int,
            default=None,
            help="Aufbewahrungsfrist in Monaten (Vorgabe: DATA_RETENTION_MONTHS).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Die Konten tatsaechlich anonymisieren.",
        )

    def handle(self, *args, **options):
        months = options["months"]
        cutoff = retention.retention_cutoff(months)
        found = retention.candidates(months)

        self.stdout.write(f"Stichtag: {cutoff:%d.%m.%Y}. Betroffene Konten: {len(found)}.")
        for candidate in found:
            last = (
                f"letzte Zeit am {candidate.last_entry:%d.%m.%Y}"
                if candidate.last_entry
                else "keine Zeiteintraege"
            )
            self.stdout.write(f"  Konto {candidate.user.pk}: {last}")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("Nichts geaendert. Mit --apply werden die Konten anonymisiert.")
            )
            return

        count = 0
        for candidate in found:
            retention.anonymize(candidate.user)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} Konten anonymisiert."))
