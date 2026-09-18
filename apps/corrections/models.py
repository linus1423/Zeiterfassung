from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils.dateparse import parse_datetime


class CorrectionRequest(models.Model):
    """Antrag auf Korrektur der eigenen Zeiterfassung.

    Nutzer aendern ihre Zeiten nie selbst. Sie beantragen die Aenderung, ein
    Admin der Gruppe entscheidet darueber.
    """

    class Kind(models.TextChoices):
        EDIT = "edit", "Aenderung"
        CREATE = "create", "Nachtrag"
        DELETE = "delete", "Loeschung"

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        APPROVED = "approved", "Genehmigt"
        REJECTED = "rejected", "Abgelehnt"
        WITHDRAWN = "withdrawn", "Zurueckgezogen"

    time_entry = models.ForeignKey(
        "tracking.TimeEntry",
        verbose_name="Zeiteintrag",
        # SET_NULL, damit ein genehmigter Loeschantrag als Beleg erhalten
        # bleibt, auch wenn der Zeiteintrag selbst verschwindet.
        on_delete=models.SET_NULL,
        related_name="correction_requests",
        null=True,
        blank=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Antragsteller",
        on_delete=models.CASCADE,
        related_name="correction_requests",
    )
    group = models.ForeignKey(
        "groups.Group",
        verbose_name="Gruppe",
        on_delete=models.PROTECT,
        related_name="correction_requests",
    )
    kind = models.CharField("Art", max_length=10, choices=Kind.choices)
    proposed_start = models.DateTimeField("Gewuenschter Beginn", null=True, blank=True)
    proposed_end = models.DateTimeField("Gewuenschtes Ende", null=True, blank=True)
    proposed_activity = models.ForeignKey(
        "groups.Activity",
        verbose_name="Gewuenschte Taetigkeit",
        on_delete=models.PROTECT,
        related_name="correction_requests",
        null=True,
        blank=True,
    )
    proposed_breaks = models.JSONField("Gewuenschte Pausen", default=list, blank=True)
    reason = models.TextField("Begruendung")
    status = models.CharField(
        "Status", max_length=10, choices=Status.choices, default=Status.PENDING
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Entschieden von",
        on_delete=models.SET_NULL,
        related_name="decided_corrections",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField("Entschieden am", null=True, blank=True)
    decision_note = models.TextField("Begruendung der Entscheidung", blank=True)
    decision_seen_at = models.DateTimeField(
        "Entscheidung gesehen am",
        null=True,
        blank=True,
        help_text="Solange leer, zaehlt die Entscheidung in der Navigation als neu.",
    )
    created_at = models.DateTimeField("Gestellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Korrekturantrag"
        verbose_name_plural = "Korrekturantraege"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "group"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} von {self.requested_by} ({self.get_status_display()})"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_decided(self) -> bool:
        return self.status in (self.Status.APPROVED, self.Status.REJECTED)

    @property
    def proposed_break_periods(self) -> list[dict]:
        """Die beantragten Pausen als Zeitpunkte, fuer die Anzeige im Antrag."""
        periods = []
        for item in self.proposed_breaks or []:
            start = parse_datetime(item.get("start") or "")
            end = parse_datetime(item.get("end") or "")
            if start and end:
                periods.append({"start": start, "end": end, "duration": end - start})
        periods.sort(key=lambda item: item["start"])
        return periods

    @property
    def proposed_break_total(self) -> timedelta:
        return sum(
            (period["duration"] for period in self.proposed_break_periods),
            timedelta(),
        )
