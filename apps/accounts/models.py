from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Eigenes Nutzermodell.

    Die Rolle "Buchhaltung" haengt am Nutzer, weil sie fuer die gesamte
    Installation gilt und nicht an eine Gruppe gebunden ist. Die Rolle
    "Gruppen-Admin" steht dagegen in GroupMembership.
    """

    email = models.EmailField("E-Mail-Adresse", unique=True)
    display_name = models.CharField("Anzeigename", max_length=150, blank=True)
    personnel_number = models.CharField("Personalnummer", max_length=50, blank=True)
    is_accounting = models.BooleanField(
        "Buchhaltung",
        default=False,
        help_text="Darf die Zeiten aller Gruppen lesen und exportieren, aber nichts aendern.",
    )

    class Meta:
        verbose_name = "Nutzer"
        verbose_name_plural = "Nutzer"
        ordering = ["last_name", "first_name", "username"]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        if self.display_name:
            return self.display_name
        name = f"{self.first_name} {self.last_name}".strip()
        return name or self.username or self.email

    @property
    def sees_all_groups(self) -> bool:
        """Buchhaltung und System-Admins sehen gruppenuebergreifend."""
        return self.is_accounting or self.is_superuser

    def memberships(self):
        from apps.groups.models import GroupMembership

        return GroupMembership.objects.filter(user=self, group__is_active=True)

    def member_group_ids(self) -> list[int]:
        return list(self.memberships().values_list("group_id", flat=True))

    def admin_group_ids(self) -> list[int]:
        from apps.groups.models import GroupMembership

        return list(
            self.memberships()
            .filter(role=GroupMembership.Role.ADMIN)
            .values_list("group_id", flat=True)
        )

    def administrated_group_ids(self) -> list[int]:
        """Gruppen, deren Korrekturantraege der Nutzer bearbeiten darf.

        Fuer einen System-Admin sind das alle aktiven Gruppen, passend zu
        `is_group_admin`. Genau dafuer gibt es ihn: stellt der einzige Admin
        einer Gruppe selbst einen Antrag, entscheidet der System-Admin.
        """
        from apps.groups.models import Group

        if self.is_superuser:
            return list(Group.objects.filter(is_active=True).values_list("pk", flat=True))
        return self.admin_group_ids()

    def is_group_admin(self, group) -> bool:
        from apps.groups.models import GroupMembership

        if self.is_superuser:
            return True
        return self.memberships().filter(group=group, role=GroupMembership.Role.ADMIN).exists()

    def is_group_member(self, group) -> bool:
        return self.memberships().filter(group=group).exists()

    @property
    def is_any_group_admin(self) -> bool:
        from apps.groups.models import GroupMembership

        if self.is_superuser:
            from apps.groups.models import Group

            return Group.objects.filter(is_active=True).exists()
        return self.memberships().filter(role=GroupMembership.Role.ADMIN).exists()
