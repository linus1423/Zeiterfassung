"""Summen für die Auswertungsseite (Issue 56).

Die Zahlen entstehen aus derselben Verdichtung wie der Export
(apps/reporting/services.py). Die Zeilen werden genau einmal gebaut und
danach mehrfach verdichtet, damit die Seite auch bei einigen hundert Nutzern
über ein Jahr mit einer einzigen Abfrage auskommt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import charts
from .services import aggregate, to_hhmm, to_hours


def _activity_label(row: dict) -> str:
    """Tätigkeiten gehören je einer Gruppe, deshalb steht die Gruppe davor."""
    return f"{row['group']} · {row['activity']}"


def _user_label(row: dict) -> str:
    return row["full_name"] or row["email"]


@dataclass(frozen=True)
class SectionDefinition:
    """Ein Abschnitt der Seite: Verdichtung, Überschriften und Beschriftung."""

    key: str
    title: str
    chart_title: str
    column: str
    label_of: Callable[[dict], str]


SECTIONS: tuple[SectionDefinition, ...] = (
    SectionDefinition(
        "activity", "Je Tätigkeit", "Stunden je Tätigkeit", "Gruppe und Tätigkeit", _activity_label
    ),
    SectionDefinition("user", "Je Nutzer", "Stunden je Nutzer", "Nutzer", _user_label),
    SectionDefinition(
        "group", "Je Gruppe", "Stunden je Gruppe", "Gruppe", lambda row: row["group"]
    ),
    SectionDefinition(
        "cost_center",
        "Je Kostenstelle",
        "Stunden je Kostenstelle",
        "Kostenstelle",
        lambda row: row["cost_center"],
    ),
)


@dataclass(frozen=True)
class SummaryRow:
    """Eine Zeile einer Summenübersicht."""

    label: str
    seconds: float
    entry_count: int
    share: float

    @property
    def hours(self) -> float:
        return to_hours(self.seconds)

    @property
    def hhmm(self) -> str:
        return to_hhmm(self.seconds)


@dataclass(frozen=True)
class SummarySection:
    """Ein Abschnitt der Auswertungsseite: Tabelle und das Bild dazu."""

    key: str
    title: str
    column: str
    rows: list[SummaryRow] = field(default_factory=list)
    chart: str = ""

    @property
    def seconds(self) -> float:
        return sum(row.seconds for row in self.rows)

    @property
    def hhmm(self) -> str:
        return to_hhmm(self.seconds)

    @property
    def hours(self) -> float:
        return to_hours(self.seconds)


@dataclass(frozen=True)
class Summary:
    """Alle Summen eines Zeitraums samt Gesamtsumme."""

    sections: list[SummarySection]
    seconds: float
    entry_count: int

    @property
    def hhmm(self) -> str:
        return to_hhmm(self.seconds)

    @property
    def hours(self) -> float:
        return to_hours(self.seconds)

    @property
    def is_empty(self) -> bool:
        return self.entry_count == 0


def _rows_of(
    base_rows: list[dict], definition: SectionDefinition, total_seconds: float
) -> list[SummaryRow]:
    """Die verdichteten Zeilen eines Abschnitts, die größte zuerst."""
    rows = [
        SummaryRow(
            label=definition.label_of(row),
            seconds=row["work_seconds"],
            entry_count=row["entry_count"],
            share=round(row["work_seconds"] / total_seconds * 100, 1) if total_seconds else 0.0,
        )
        for row in aggregate(base_rows, definition.key)
    ]
    rows.sort(key=lambda row: (-row.seconds, row.label))
    return rows


def _chart_for(definition: SectionDefinition, rows: list[SummaryRow]) -> str:
    """Das Balkendiagramm eines Abschnitts, mit begrenzter Zahl an Balken."""
    bars = charts.summarized_bars(
        [charts.Bar(label=row.label, value=row.seconds, text=row.hhmm) for row in rows],
        format_value=to_hhmm,
    )
    if not bars:
        return ""
    biggest = max(bars, key=lambda bar: bar.value)
    description = (
        f"Balkendiagramm mit {len(bars)} Balken. Die Länge eines Balkens steht für die "
        f"Arbeitszeit, angeschrieben als Stunden und Minuten. Am längsten ist "
        f"{biggest.label} mit {biggest.text}. Alle Werte stehen in der Tabelle darunter."
    )
    return charts.bar_chart(
        chart_id=f"diagramm-{definition.key}",
        title=definition.chart_title,
        description=description,
        bars=bars,
    )


def build(base_rows: list[dict]) -> Summary:
    """Baut alle Abschnitte aus den bereits erzeugten Zeilen."""
    total_seconds = sum(row["work_seconds"] for row in base_rows)
    sections = []
    for definition in SECTIONS:
        rows = _rows_of(base_rows, definition, total_seconds)
        sections.append(
            SummarySection(
                key=definition.key,
                title=definition.title,
                column=definition.column,
                rows=rows,
                chart=_chart_for(definition, rows),
            )
        )
    return Summary(sections=sections, seconds=total_seconds, entry_count=len(base_rows))
