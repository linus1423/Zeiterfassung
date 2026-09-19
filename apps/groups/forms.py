from django import forms
from django.contrib.auth import get_user_model
from django.utils.text import slugify

from .models import Activity, Group, GroupMembership
from .periods import MAX_MONTH_START_DAY

User = get_user_model()


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["name", "description", "sort_order", "is_active"]


class MembershipForm(forms.Form):
    """Nutzer per E-Mail zu einer Gruppe hinzufuegen.

    Es werden nur bestehende Konten zugeordnet. Konten entstehen beim ersten
    Login ueber den Identity-Provider.
    """

    email = forms.EmailField(label="E-Mail-Adresse des Nutzers")
    role = forms.ChoiceField(
        label="Rolle", choices=GroupMembership.Role.choices, initial=GroupMembership.Role.MEMBER
    )

    def __init__(self, group, *args, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist as exc:
            raise forms.ValidationError(
                "Es gibt kein aktives Konto mit dieser Adresse. "
                "Der Nutzer muss sich einmal anmelden, danach kann er zugeordnet werden."
            ) from exc
        if GroupMembership.objects.filter(group=self.group, user=user).exists():
            raise forms.ValidationError("Dieser Nutzer ist bereits in der Gruppe.")
        self.cleaned_user = user
        return email


class GroupForm(forms.ModelForm):
    """Neue Gruppe anlegen. Nur System-Admins duerfen das."""

    class Meta:
        model = Group
        fields = ["name", "cost_center", "month_start_day", "idp_identifier"]
        widgets = {
            "month_start_day": forms.NumberInput(
                attrs={"min": 1, "max": MAX_MONTH_START_DAY, "step": 1}
            )
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Group.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("Eine Gruppe mit diesem Namen gibt es schon.")
        # Der Kurzname wird aus dem Namen erzeugt und muss ebenfalls eindeutig
        # sein; "Werkstatt" und "werk statt" ergaeben denselben.
        if Group.objects.filter(slug=slugify(name)[:140]).exists():
            raise forms.ValidationError(
                "Aus diesem Namen entsteht derselbe Kurzname wie bei einer bestehenden Gruppe."
            )
        return name


class GroupSettingsForm(forms.ModelForm):
    """Einstellungen, die ein Admin der Gruppe selbst pflegen darf."""

    class Meta:
        model = Group
        fields = ["month_start_day", "cost_center", "idp_identifier"]
        widgets = {
            "month_start_day": forms.NumberInput(
                attrs={"min": 1, "max": MAX_MONTH_START_DAY, "step": 1}
            )
        }


class ClosePeriodForm(forms.Form):
    """Abschluss eines Zeitraums, identifiziert ueber seinen ersten Tag."""

    period_start = forms.DateField(widget=forms.HiddenInput())
    note = forms.CharField(
        label="Bemerkung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
