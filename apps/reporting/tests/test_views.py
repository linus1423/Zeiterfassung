from django.urls import reverse

from apps.reporting.models import ExportProfile


def test_plain_member_has_no_access_to_reporting(client, member):
    client.force_login(member)

    response = client.get(reverse("reporting:export"))

    assert response.status_code == 403


def test_accountant_opens_reporting(client, accountant):
    client.force_login(accountant)

    response = client.get(reverse("reporting:export"))

    assert response.status_code == 200


def test_group_admin_opens_reporting(client, group_admin):
    client.force_login(group_admin)

    assert client.get(reverse("reporting:export")).status_code == 200


def test_csv_download_has_attachment_header(client, accountant):
    client.force_login(accountant)

    response = client.post(
        reverse("reporting:export"),
        {
            "start": "2026-01-01",
            "end": "2026-12-31",
            "grouping": "entry",
            "columns": ["full_name", "hours"],
            "csv_dialect": "de",
            "action": "csv",
        },
    )

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Content-Disposition"].endswith('.csv"')


def test_xlsx_download_is_an_excel_file(client, accountant):
    client.force_login(accountant)

    response = client.post(
        reverse("reporting:export"),
        {
            "start": "2026-01-01",
            "end": "2026-12-31",
            "grouping": "entry",
            "columns": ["full_name", "hours"],
            "action": "xlsx",
        },
    )

    assert response.status_code == 200
    assert response["Content-Type"].endswith("spreadsheetml.sheet")
    assert response.content[:2] == b"PK"


def test_saving_a_profile_keeps_columns_and_grouping(client, accountant):
    client.force_login(accountant)

    client.post(
        reverse("reporting:export"),
        {
            "start": "2026-01-01",
            "end": "2026-01-31",
            "grouping": "user_month",
            "columns": ["full_name", "hours"],
            "action": "save_profile",
            "name": "Monatsabrechnung",
        },
        follow=True,
    )

    profile = ExportProfile.objects.get(owner=accountant, name="Monatsabrechnung")
    assert profile.columns == ["full_name", "hours"]
    assert profile.grouping == "user_month"


def test_profile_of_another_user_stays_private(client, accountant, group_admin):
    private = ExportProfile.objects.create(
        owner=accountant, name="Privat", columns=["hours"], grouping="entry"
    )
    client.force_login(group_admin)

    response = client.get(reverse("reporting:export_profile", args=[private.pk]))

    assert response.status_code == 404
