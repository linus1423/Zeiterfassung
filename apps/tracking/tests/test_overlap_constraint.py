"""Überschneidungsfreiheit in der Datenbank (Issue 43).

Die Ausschlussbedingung selbst gibt es nur unter PostgreSQL; die Tests laufen
unter SQLite. Geprüft wird deshalb, was hier prüfbar ist: dass die Migration
nur unter PostgreSQL etwas tut und das richtige SQL absetzt, und dass die
Anwendung den Datenbankfehler in eine lesbare Meldung übersetzt, statt mit
einem Serverfehler auszusteigen.
"""

from importlib import import_module

import pytest
from django.db import IntegrityError

from apps.corrections.services import CorrectionError
from apps.tracking import services
from apps.tracking.entries import (
    OVERLAP_CONSTRAINT,
    is_overlap_violation,
    overlap_guard,
)
from apps.tracking.entry_editing import EntryEditError
from apps.tracking.models import TimeEntry

migration = import_module("apps.tracking.migrations.0003_timeentry_no_overlap")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, vendor, rows):
        self.vendor = vendor
        self.last_cursor = None
        self._rows = rows

    def cursor(self):
        self.last_cursor = FakeCursor(self._rows)
        return self.last_cursor


class FakeSchemaEditor:
    """Nur so viel, wie die Migration von einem Schema-Editor benutzt."""

    def __init__(self, vendor="postgresql", rows=()):
        self.connection = FakeConnection(vendor, list(rows))
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(str(sql))


def test_migration_does_nothing_on_sqlite():
    editor = FakeSchemaEditor(vendor="sqlite")

    migration.add_constraint(None, editor)
    migration.drop_constraint(None, editor)

    assert editor.executed == []
    assert editor.connection.last_cursor is None


def test_migration_adds_the_exclusion_constraint_on_postgres():
    editor = FakeSchemaEditor()

    migration.add_constraint(None, editor)

    assert len(editor.executed) == 1
    sql = " ".join(editor.executed[0].split())
    assert f"ALTER TABLE {migration.TABLE} ADD CONSTRAINT {OVERLAP_CONSTRAINT}" in sql
    assert "EXCLUDE USING gist" in sql
    assert "user_id WITH =" in sql
    assert 'tstzrange("start", "end") WITH &&' in sql


def test_migration_reports_existing_overlaps_instead_of_failing_blindly():
    editor = FakeSchemaEditor(rows=[(7, 9)])

    with pytest.raises(RuntimeError) as excinfo:
        migration.add_constraint(None, editor)

    assert "7 und 9" in str(excinfo.value)
    assert editor.executed == [], "ohne saubere Daten wird die Bedingung nicht angelegt"


def test_migration_drops_the_constraint_backwards():
    editor = FakeSchemaEditor()

    migration.drop_constraint(None, editor)

    assert OVERLAP_CONSTRAINT in editor.executed[0]
    assert "DROP CONSTRAINT" in editor.executed[0]


def test_recognises_only_the_overlap_constraint():
    assert is_overlap_violation(IntegrityError(f'verletzt "{OVERLAP_CONSTRAINT}"'))
    assert not is_overlap_violation(IntegrityError('verletzt "time_entry_end_after_start"'))


@pytest.mark.django_db
def test_guard_turns_the_constraint_into_a_readable_message():
    with pytest.raises(EntryEditError) as excinfo:  # noqa: PT012
        with overlap_guard(EntryEditError):
            raise IntegrityError(f'... "{OVERLAP_CONSTRAINT}" ...')

    assert "überschneidet sich" in str(excinfo.value)


@pytest.mark.django_db
def test_guard_passes_other_database_errors_on():
    with pytest.raises(IntegrityError):  # noqa: PT012
        with overlap_guard(CorrectionError):
            raise IntegrityError('... "time_entry_end_after_start" ...')


@pytest.mark.django_db
def test_clock_in_names_the_conflicting_time(monkeypatch, member, group, activity):
    def refuse(**kwargs):
        raise IntegrityError(f'... "{OVERLAP_CONSTRAINT}" ...')

    monkeypatch.setattr(TimeEntry.objects, "create", refuse)

    with pytest.raises(services.ClockError) as excinfo:
        services.clock_in(member, group, activity)

    assert "bereits eine Zeit erfasst" in str(excinfo.value)


@pytest.mark.django_db
def test_clock_in_still_reports_a_second_click_as_already_clocked_in(
    monkeypatch, member, group, activity
):
    def refuse(**kwargs):
        raise IntegrityError('... "unique_open_time_entry_per_user" ...')

    monkeypatch.setattr(TimeEntry.objects, "create", refuse)

    with pytest.raises(services.ClockError) as excinfo:
        services.clock_in(member, group, activity)

    assert "bereits eingestempelt" in str(excinfo.value)
