"""Die Oberfläche des CSV-Imports: Rechte, Vorschau und zweiter Schritt (Issue 54)."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.tracking.models import EntryImport, TimeEntry

from .test_csv_import import csv_bytes, row


def upload(data: bytes, name: str = "zeiten.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="text/csv")


@pytest.mark.parametrize("role", ["member", "group_admin", "accountant"])
def test_only_system_admins_may_import(client, request, role, group):
    person = request.getfixturevalue(role)
    client.force_login(person)

    assert client.get(reverse("tracking:import_entries")).status_code == 403
    assert client.get(reverse("tracking:import_sample")).status_code == 403


def test_page_is_open_for_system_admins(client, superuser):
    client.force_login(superuser)

    response = client.get(reverse("tracking:import_entries"))

    assert response.status_code == 200
    assert "Zeiten importieren" in response.content.decode()


def test_sample_download(client, superuser):
    client.force_login(superuser)

    response = client.get(reverse("tracking:import_sample"))

    assert response.status_code == 200
    assert response["Content-Disposition"].endswith('filename="zeiten-vorlage.csv"')
    assert response.content.startswith(b"\xef\xbb\xbf")


def test_preview_writes_nothing(client, superuser, member, group, activity):
    client.force_login(superuser)

    response = client.post(reverse("tracking:import_entries"), {"datei": upload(csv_bytes(row()))})

    assert response.status_code == 200
    plan = response.context["plan"]
    assert plan.ok
    assert len(plan.entries) == 1
    assert [person.full_name for person in plan.users] == [member.full_name]
    assert [item.name for item in plan.groups] == [group.name]
    assert TimeEntry.objects.count() == 0
    assert EntryImport.objects.get().status == EntryImport.Status.PREPARED


def test_preview_lists_errors_per_line(client, superuser, member, group, activity):
    client.force_login(superuser)

    response = client.post(
        reverse("tracking:import_entries"),
        {"datei": upload(csv_bytes(row(), row(group="Kantine")))},
    )

    plan = response.context["plan"]
    assert [error.line for error in plan.errors] == [3]
    assert "Kantine" in response.content.decode()


def test_second_step_writes(client, superuser, member, group, activity):
    client.force_login(superuser)
    client.post(reverse("tracking:import_entries"), {"datei": upload(csv_bytes(row()))})
    record = EntryImport.objects.get()

    response = client.post(reverse("tracking:import_apply", args=[record.pk]), follow=True)

    assert response.status_code == 200
    assert TimeEntry.objects.get().source == TimeEntry.Source.IMPORT
    record.refresh_from_db()
    assert record.status == EntryImport.Status.APPLIED
    # Die Datei wird nach der Übernahme nicht weiter aufbewahrt.
    assert bytes(record.payload) == b""
    assert record.summary["Zeiten"] == 1


def test_a_prepared_import_can_be_applied_only_once(client, superuser, member, group, activity):
    client.force_login(superuser)
    client.post(reverse("tracking:import_entries"), {"datei": upload(csv_bytes(row()))})
    record = EntryImport.objects.get()
    client.post(reverse("tracking:import_apply", args=[record.pk]))

    response = client.post(reverse("tracking:import_apply", args=[record.pk]))

    assert response.status_code == 404
    assert TimeEntry.objects.count() == 1


def test_a_foreign_import_cannot_be_applied(client, superuser, make_user, member, group, activity):
    client.force_login(superuser)
    client.post(reverse("tracking:import_entries"), {"datei": upload(csv_bytes(row()))})
    record = EntryImport.objects.get()

    other = make_user("zweiter@example.com", is_superuser=True, is_staff=True)
    client.force_login(other)
    response = client.post(reverse("tracking:import_apply", args=[record.pk]))

    assert response.status_code == 404
    assert TimeEntry.objects.count() == 0


def test_a_file_with_the_wrong_extension_is_refused(client, superuser):
    client.force_login(superuser)

    response = client.post(
        reverse("tracking:import_entries"), {"datei": upload(b"egal", name="zeiten.xlsx")}
    )

    assert response.status_code == 200
    assert response.context["plan"] is None
    assert "Endung .csv" in response.content.decode()


def test_navigation_shows_the_import_only_to_system_admins(client, superuser, member):
    client.force_login(member)
    assert "Import</a>" not in client.get(reverse("tracking:clock")).content.decode()

    client.force_login(superuser)
    assert "Import</a>" in client.get(reverse("tracking:clock")).content.decode()
