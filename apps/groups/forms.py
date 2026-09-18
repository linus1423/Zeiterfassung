from django import forms
from django.contrib.auth import get_user_model

from .models import Activity, GroupMembership

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
