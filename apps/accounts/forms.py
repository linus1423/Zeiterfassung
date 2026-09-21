from django import forms
from django.contrib.auth import get_user_model

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
