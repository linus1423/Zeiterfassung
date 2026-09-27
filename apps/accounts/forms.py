from django import forms
from django.contrib.auth import get_user_model

from apps.groups.membership import removal_blocker, role_change_blocker
from apps.groups.models import Group, GroupMembership

User = get_user_model()


class UserStammdatenForm(forms.ModelForm):
    """Was ein System-Admin an einem Konto pflegen darf.

    Name und E-Mail kommen aus dem Identity-Provider und bleiben deshalb
    unveränderlich. Die Personalnummer kennt nur die Personalverwaltung, und
    die Rolle Buchhaltung gilt für die ganze Installation; beides gibt es
    nirgends sonst in der Oberfläche.
    """

    class Meta:
        model = User
        fields = ["personnel_number", "is_accounting"]

    def clean_personnel_number(self):
        number = (self.cleaned_data.get("personnel_number") or "").strip()
        if not number:
            return ""
        doppelt = (
            User.objects.filter(personnel_number__iexact=number)
            .exclude(pk=self.instance.pk)
            .first()
        )
        if doppelt is not None:
            raise forms.ValidationError(f"Diese Personalnummer hat bereits {doppelt.full_name}.")
        return number


NO_MEMBERSHIP = ""


class UserGroupsForm(forms.Form):
    """Gruppenzugehörigkeit eines Kontos, eine Auswahl je aktiver Gruppe.

    Es gelten dieselben Grenzen wie auf der Mitgliederseite einer Gruppe
    (apps/groups/membership.py). Verletzt eine Änderung sie, wird gar nichts
    gespeichert, damit kein halber Stand entsteht.
    """

    ROLE_CHOICES = [(NO_MEMBERSHIP, "Kein Mitglied"), *GroupMembership.Role.choices]

    def __init__(self, person, *args, **kwargs):
        self.person = person
        super().__init__(*args, **kwargs)
        self.groups = list(Group.objects.filter(is_active=True).order_by("name"))
        self.existing = {
            membership.group_id: membership
            for membership in GroupMembership.objects.filter(
                user=person, group__in=self.groups
            ).select_related("group")
        }
        for group in self.groups:
            membership = self.existing.get(group.pk)
            help_text = ""
            if membership is not None and membership.from_idp:
                help_text = "Aus dem Identity-Provider; der nächste Login kann das überschreiben."
            self.fields[self.field_name(group)] = forms.ChoiceField(
                label=group.name,
                choices=self.ROLE_CHOICES,
                required=False,
                initial=membership.role if membership else NO_MEMBERSHIP,
                help_text=help_text,
            )

    @staticmethod
    def field_name(group) -> str:
        return f"gruppe_{group.pk}"

    def clean(self):
        cleaned = super().clean()
        self.to_add, self.to_change, self.to_remove = [], [], []
        for group in self.groups:
            name = self.field_name(group)
            # Eine Gruppe, die beim Laden der Seite noch nicht da war, fehlt
            # im Formular. Das heißt "nicht angefasst", nicht "entfernen".
            if name not in self.data or name not in cleaned:
                continue
            role = cleaned[name]
            membership = self.existing.get(group.pk)
            if membership is None:
                if role:
                    self.to_add.append((group, role))
            elif not role:
                blocker = removal_blocker(membership)
                if blocker:
                    self.add_error(name, blocker)
                else:
                    self.to_remove.append(membership)
            elif role != membership.role:
                blocker = role_change_blocker(membership, role)
                if blocker:
                    self.add_error(name, blocker)
                else:
                    self.to_change.append((membership, role))
        return cleaned

    @staticmethod
    def describe(memberships) -> dict[str, str]:
        return {m.group.name: m.get_role_display() for m in memberships}

    def save(self) -> dict[str, dict[str, str]] | None:
        """Übernimmt die Auswahl. Gibt vorher/nachher zurück, oder None ohne Änderung."""
        if not (self.to_add or self.to_change or self.to_remove):
            return None
        before = self.describe(self.existing.values())
        for group, role in self.to_add:
            GroupMembership.objects.create(user=self.person, group=group, role=role)
        for membership, role in self.to_change:
            membership.role = role
            membership.save(update_fields=["role"])
        for membership in self.to_remove:
            membership.delete()
        after = self.describe(
            GroupMembership.objects.filter(user=self.person, group__in=self.groups).select_related(
                "group"
            )
        )
        return {"vorher": before, "nachher": after}


class UserSearchForm(forms.Form):
    q = forms.CharField(label="Suche", required=False, max_length=100)


class EmergencyLoginForm(forms.Form):
    """Benutzername und Passwort für den Notfallzugang.

    Bewusst ohne jede eigene Prüflogik: das Formular sammelt nur ein, ob ein
    Konto existiert und ob es ein System-Admin ist, entscheidet allein die
    View (siehe apps/accounts/views.emergency_login). So kann das Formular
    auch nichts darüber verraten.

    Das Passwort ist auf 128 Zeichen begrenzt. Das ist keine fachliche Grenze,
    sondern hält die Kosten des Hashens klein: ein beliebig langes Passwort
    wäre sonst ein billiger Weg, Rechenzeit zu verbrennen.
    """

    username = forms.CharField(label="Benutzername", max_length=150, strip=True)
    password = forms.CharField(
        label="Passwort",
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
    )
