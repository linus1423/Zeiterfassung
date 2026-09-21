from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Unveränderliches Protokoll aller Änderungen an Zeitdaten.

    Arbeitszeiten müssen nachvollziehbar sein: wer hat wann was geändert.
    Einträge werden nur geschrieben, nie geändert oder gelöscht.
    """

    class Action(models.TextChoices):
        CLOCK_IN = "clock_in", "Eingestempelt"
        CLOCK_OUT = "clock_out", "Ausgestempelt"
        BREAK_START = "break_start", "Pause begonnen"
        BREAK_END = "break_end", "Pause beendet"
        AUTO_CLOSE = "auto_close", "Automatisch beendet"
        ENTRY_UPDATED = "entry_updated", "Zeiteintrag geändert"
        ENTRY_DELETED = "entry_deleted", "Zeiteintrag gelöscht"
        ENTRIES_IMPORTED = "entries_imported", "Zeiten importiert"
        CORRECTION_REQUESTED = "correction_requested", "Korrektur beantragt"
        CORRECTION_APPROVED = "correction_approved", "Korrektur genehmigt"
        CORRECTION_REJECTED = "correction_rejected", "Korrektur abgelehnt"
        CORRECTION_WITHDRAWN = "correction_withdrawn", "Korrektur zurückgezogen"
        EXPORT = "export", "Export erstellt"
        PERIOD_CLOSED = "period_closed", "Zeitraum abgeschlossen"
        PERIOD_REOPENED = "period_reopened", "Zeitraum wieder geöffnet"
        USER_ANONYMIZED = "user_anonymized", "Konto anonymisiert"
        USER_UPDATED = "user_updated", "Stammdaten geändert"
        MEMBERSHIP_SYNCED = "membership_synced", "Mitgliedschaften abgeglichen"
        GROUP_CREATED = "group_created", "Gruppe angelegt"
        GROUP_CHANGE_REQUESTED = "group_change_requested", "Gruppenwechsel beantragt"
        GROUP_CHANGE_APPROVED = "group_change_approved", "Gruppenwechsel genehmigt"
        GROUP_CHANGE_REJECTED = "group_change_rejected", "Gruppenwechsel abgelehnt"
        GROUP_CHANGE_WITHDRAWN = "group_change_withdrawn", "Gruppenwechsel zurückgezogen"
        NOTIFICATION_FAILED = "notification_failed", "Benachrichtigung fehlgeschlagen"
        EMERGENCY_LOGIN = "emergency_login", "Notfallzugang genutzt"
        EMERGENCY_LOGIN_FAILED = "emergency_login_failed", "Notfallzugang gescheitert"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Handelnde Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    action = models.CharField("Aktion", max_length=32, choices=Action.choices)
    target_type = models.CharField("Objektart", max_length=64, blank=True)
    target_id = models.PositiveBigIntegerField("Objekt-Id", null=True, blank=True)
    group = models.ForeignKey(
        "groups.Group",
        verbose_name="Gruppe",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Betroffene Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries_about_me",
    )
    changes = models.JSONField("Änderungen", default=dict, blank=True)
    note = models.TextField("Bemerkung", blank=True)
    created_at = models.DateTimeField("Zeitpunkt", auto_now_add=True)

    class Meta:
        verbose_name = "Protokolleintrag"
        verbose_name_plural = "Protokoll"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} ({self.created_at:%d.%m.%Y %H:%M})"


def log(action, *, actor=None, target=None, group=None, subject=None, changes=None, note=""):
    """Schreibt einen Protokolleintrag. Bewusst tolerant, damit das Protokoll
    nie den eigentlichen Vorgang verhindert, aber immer geschrieben wird."""
    return AuditLog.objects.create(
        actor=actor if (actor is not None and actor.is_authenticated) else None,
        action=action,
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=getattr(target, "pk", None),
        group=group,
        subject=subject,
        changes=changes or {},
        note=note,
    )
