"""Zeiten aus einer CSV-Datei importieren, von der Kommandozeile aus (Issue 54).

Dasselbe Verfahren wie in der Oberfläche: ohne --uebernehmen wird nur geprüft
und berichtet, erst mit --uebernehmen wird geschrieben, und dann alles oder
nichts. Gedacht für die Einführung, wenn eine Liste aus dem Altsystem einmalig
einzuspielen ist und niemand sie durch den Browser schieben will.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.tracking import csv_import


class Command(BaseCommand):
    help = "Importiert Zeiten aus einer CSV-Datei. Ohne --uebernehmen wird nur geprüft."

    def add_arguments(self, parser):
        parser.add_argument("datei", help="Pfad zur CSV-Datei (Semikolon, UTF-8).")
        parser.add_argument(
            "--uebernehmen",
            action="store_true",
            help="Die Zeiten wirklich schreiben. Ohne diesen Schalter wird nur geprüft.",
        )
        parser.add_argument(
            "--akteur",
            default="",
            help=(
                "E-Mail-Adresse des System-Admins, der im Protokoll als handelnde "
                "Person steht. Ohne Angabe bleibt sie leer."
            ),
        )

    def _actor(self, email: str):
        if not email:
            return None
        person = get_user_model().objects.filter(email__iexact=email).first()
        if person is None:
            raise CommandError(f"Es gibt kein Konto mit der E-Mail-Adresse {email}.")
        if not person.is_superuser:
            raise CommandError(f"{person.full_name} ist kein System-Admin.")
        return person

    def _report(self, plan) -> None:
        self.stdout.write(
            f"{plan.row_count} Zeilen gelesen, {len(plan.entries)} Zeiten prüfbar, "
            f"{plan.error_count} fehlerhaft."
        )
        people = ", ".join(person.full_name for person in plan.users) or "keine"
        groups = ", ".join(group.name for group in plan.groups) or "keine"
        self.stdout.write(f"Nutzer: {people}")
        self.stdout.write(f"Gruppen: {groups}")
        if plan.first_day:
            self.stdout.write(
                f"Zeitraum: {plan.first_day:%d.%m.%Y} bis {plan.last_day:%d.%m.%Y}, "
                f"{plan.total_hours} Stunden."
            )
        for error in plan.errors:
            self.stdout.write(self.style.ERROR(f"Zeile {error.line}: {error.message}"))
        if plan.truncated:
            self.stdout.write(self.style.WARNING("Es werden nur die ersten Fehler gezeigt."))

    def handle(self, *args, **options):
        path = Path(options["datei"])
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CommandError(f"Die Datei ist nicht lesbar: {exc}") from exc

        actor = self._actor(options["akteur"].strip())

        if not options["uebernehmen"]:
            try:
                plan = csv_import.prepare(data)
            except csv_import.CsvImportError as exc:
                raise CommandError(str(exc)) from exc
            self._report(plan)
            if plan.ok:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Die Datei ist fehlerfrei. Mit --uebernehmen wird sie geschrieben."
                    )
                )
            else:
                raise CommandError("Die Datei hat Fehler. Es wurde nichts geschrieben.")
            return

        try:
            result = csv_import.run(data, actor=actor)
        except csv_import.CsvImportError as exc:
            if exc.plan is not None:
                self._report(exc.plan)
            raise CommandError(str(exc)) from exc

        self._report(result.plan)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.count} Zeiten importiert, {result.plan.total_hours} Stunden."
            )
        )
