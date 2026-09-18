"""Gemeinsame Test-Fixtures."""

import pytest
from django.contrib.auth import get_user_model

from apps.groups.models import Activity, Group, GroupMembership

User = get_user_model()


@pytest.fixture(autouse=True)
def static_storage_without_manifest(settings):
    """Im Test gibt es kein collectstatic, deshalb ohne Manifest ausliefern."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def make_user(db):
    def _make(email="person@example.com", **kwargs):
        return User.objects.create_user(
            username=email.split("@")[0],
            email=email,
            password="test-passwort-1234",  # noqa: S106
            **kwargs,
        )

    return _make


@pytest.fixture
def group(db):
    return Group.objects.create(name="Werkstatt", cost_center="4711")


@pytest.fixture
def other_group(db):
    return Group.objects.create(name="Buero")


@pytest.fixture
def activity(group):
    return Activity.objects.create(group=group, name="Montage")


@pytest.fixture
def member(make_user, group):
    user = make_user("mitglied@example.com", first_name="Mia", last_name="Mitglied")
    GroupMembership.objects.create(user=user, group=group, role=GroupMembership.Role.MEMBER)
    return user


@pytest.fixture
def group_admin(make_user, group):
    user = make_user("admin@example.com", first_name="Alex", last_name="Admin")
    GroupMembership.objects.create(user=user, group=group, role=GroupMembership.Role.ADMIN)
    return user


@pytest.fixture
def accountant(make_user):
    return make_user("buchhaltung@example.com", last_name="Buch", is_accounting=True)
