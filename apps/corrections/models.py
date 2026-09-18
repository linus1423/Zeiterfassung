from django.conf import settings
from django.db import models


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
