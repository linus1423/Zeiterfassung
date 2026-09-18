from django.test import override_settings
from django.urls import reverse

from apps.accounts.adapters import NoLocalSignupAdapter, OIDCSocialAccountAdapter

BOTH_PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "keycloak",
                "name": "Keycloak",
                "client_id": "demo",
                "secret": "geheim",
                "settings": {"server_url": "https://kc.example.com/realms/demo"},
            },
            {
                "provider_id": "entra",
                "name": "Microsoft Entra ID",
                "client_id": "demo2",
                "secret": "geheim",
                "settings": {
                    "server_url": "https://login.microsoftonline.com/abc/v2.0/"
                    ".well-known/openid-configuration"
                },
            },
        ]
    }
}


@override_settings(SOCIALACCOUNT_PROVIDERS={"openid_connect": {"APPS": []}})
def test_login_page_without_providers_explains_the_situation(client, db):
    response = client.get(reverse("accounts:login"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "noch kein Anmeldeverfahren" in body


@override_settings(SOCIALACCOUNT_PROVIDERS=BOTH_PROVIDERS)
def test_login_page_offers_keycloak_and_entra(client, db):
    response = client.get(reverse("accounts:login"))
    body = response.content.decode()

    assert "Weiter mit Keycloak" in body
    assert "Weiter mit Microsoft Entra ID" in body


def test_login_page_redirects_when_already_signed_in(client, member):
    client.force_login(member)

    response = client.get(reverse("accounts:login"))

    assert response.status_code == 302
    assert response["Location"] == reverse("tracking:clock")


def test_local_signup_is_closed():
    assert NoLocalSignupAdapter().is_open_for_signup(request=None) is False


def test_signup_over_a_provider_stays_open():
    assert OIDCSocialAccountAdapter().is_open_for_signup(request=None, sociallogin=None) is True


def test_profile_page_lists_own_groups(client, member, group):
    client.force_login(member)

    response = client.get(reverse("accounts:profile"))

    assert response.status_code == 200
    assert group.name in response.content.decode()
