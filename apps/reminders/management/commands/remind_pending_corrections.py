from django.conf import settings
from django.core.management.base import BaseCommand

from apps.reminders import jobs


class Command(BaseCommand):
    help = (
        "Erinnert Gruppen-Admins an Korrekturanträge, die zu lange offen liegen, "
        "und eskaliert sie nach längerer Frist an die System-Admins."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Frist in Tagen (Vorgabe: PENDING_CORRECTION_REMINDER_DAYS).",
        )
        parser.add_argument(
            "--escalation-days",
            type=int,
            default=None,
            help=(
                "Zweite Frist in Tagen, nach der zusätzlich die System-Admins "
                "erfahren (Vorgabe: PENDING_CORRECTION_ESCALATION_DAYS)."
            ),
        )

    def handle(self, *args, **options):
        days = options["days"] or settings.PENDING_CORRECTION_REMINDER_DAYS
        escalation_days = options["escalation_days"] or settings.PENDING_CORRECTION_ESCALATION_DAYS
        if escalation_days < days:
            self.stdout.write(
                self.style.WARNING(
                    f"Die zweite Frist von {escalation_days} Tagen liegt unter der ersten "
                    f"({days}). Es gilt die erste Frist; die Eskalation kommt dann "
                    "zusammen mit der Erinnerung an die Gruppe."
                )
            )
        result = jobs.remind_pending_corrections(options["days"], options["escalation_days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} Erinnerungen angelegt, {result.mailed} versandt, "
                f"{result.resolved} erledigt."
            )
        )
