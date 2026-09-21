"""Erinnerungen, bevor ein Fehler entsteht (Issue 34).

Benachrichtigt wurde bisher nur nachträglich. Hier entstehen Hinweise, die
Arbeit ersparen: das vergessene Ausstempeln, ein liegengebliebener Antrag,
ein abgelaufener Abrechnungszeitraum.

Jede Erinnerung steht genau einmal je Anlass und Empfänger in der Tabelle.
Dafür sorgt `subject_key`, eine stabile Bezeichnung des Anlasses
("entry:12"), zusammen mit der Eindeutigkeitsbedingung. Ein wiederholter
Lauf des Diensts legt also nichts neu an, und eine weggeklickte Erinnerung
kommt nicht zurück.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class ReminderQuerySet(models.QuerySet):
    def unresolved(self):
        return self.filter(resolved_at__isnull=True)

    def for_user(self, user):
        return self.filter(recipient=user)


class Reminder(models.Model):
    """Ein Hinweis an eine Person, sichtbar im Tool und wahlweise per Mail."""

    class Kind(models.TextChoices):
        OPEN_ENTRY = "open_entry", "Ausstempeln vergessen"
        PENDING_CORRECTION = "pending_correction", "Antrag liegt offen"
        PERIOD_CLOSING = "period_closing", "Zeitraum noch nicht abgeschlossen"
        CORRECTION_ESCALATION = "correction_escalation", "Antrag bleibt liegen"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Empfänger",
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    kind = models.CharField("Art", max_length=32, choices=Kind.choices)
    subject_key = models.CharField(
        "Anlass",
        max_length=100,
        help_text="Bezeichnet den Anlass, damit dieselbe Erinnerung nur einmal entsteht.",
    )
    group = models.ForeignKey(
        "groups.Group",
        verbose_name="Gruppe",
        on_delete=models.CASCADE,
        related_name="reminders",
        null=True,
        blank=True,
    )
    message = models.TextField("Hinweis")
    url = models.CharField("Ziel", max_length=200, blank=True)
    created_at = models.DateTimeField("Entstanden am", auto_now_add=True)
    resolved_at = models.DateTimeField("Erledigt am", null=True, blank=True)
    emailed_at = models.DateTimeField("Versandt am", null=True, blank=True)

    objects = ReminderQuerySet.as_manager()

    class Meta:
        verbose_name = "Erinnerung"
        verbose_name_plural = "Erinnerungen"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "kind", "subject_key"], name="unique_reminder_per_occasion"
            )
        ]
        indexes = [models.Index(fields=["recipient", "resolved_at"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} für {self.recipient}"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def resolve(self) -> None:
        if self.resolved_at is None:
            self.resolved_at = timezone.now()
            self.save(update_fields=["resolved_at"])
