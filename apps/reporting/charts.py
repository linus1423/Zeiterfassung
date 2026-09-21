"""Balkendiagramme als eingebettetes SVG (Issue 56).

Gezeichnet wird auf dem Server, ohne Bibliothek, ohne Skript im Browser und
ohne dass die Seite etwas nachlädt. Das Bild ist nur eine zweite Darstellung:
jeder Wert steht daneben auch in einer Tabelle.

Barrierefreiheit: das SVG trägt Titel und Beschreibung und ist über
aria-labelledby damit verbunden. Balken tragen ihre Beschriftung und ihren
Wert als Text, die Grafik ist also auch ohne Farbe zu lesen.

Alle Beschriftungen kommen aus der Datenbank (Gruppen-, Tätigkeits- und
Nutzernamen). Sie werden deshalb ausnahmslos maskiert, bevor sie im Markup
landen.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import Truncator, slugify

# Mehr Balken liest ohnehin niemand, und bei einigen hundert Nutzern würde die
# Seite sonst unbrauchbar lang. Der Rest wird zu einem Balken zusammengefasst.
MAX_BARS = 12

# Längere Beschriftungen werden gekürzt; vollständig stehen sie in der Tabelle
# und im Tooltip des Balkens.
MAX_LABEL_CHARS = 30

_LABEL_WIDTH = 250
_BAR_WIDTH = 430
_VALUE_WIDTH = 120
_WIDTH = _LABEL_WIDTH + _BAR_WIDTH + _VALUE_WIDTH
_ROW_HEIGHT = 30
_BAR_HEIGHT = 18
_TOP = 10
_BOTTOM = 10


@dataclass(frozen=True)
class Bar:
    """Ein Balken: Beschriftung, Zahlenwert und der Text, der daran steht."""

    label: str
    value: float
    text: str


def summarized_bars(
    bars: list[Bar], *, format_value, max_bars: int = MAX_BARS, rest_label: str = "übrige"
) -> list[Bar]:
    """Die größten Balken, alles Weitere zu einem Balken gebündelt.

    Die Werte gehen dabei nicht verloren: was im Balken "übrige" steckt,
    steht vollständig in der Tabelle unter dem Bild.
    """
    if len(bars) <= max_bars:
        return list(bars)

    head = list(bars[: max_bars - 1])
    rest = bars[max_bars - 1 :]
    total = sum(bar.value for bar in rest)
    head.append(Bar(label=f"{rest_label} ({len(rest)})", value=total, text=format_value(total)))
    return head


def bar_chart(*, chart_id: str, title: str, description: str, bars: list[Bar]) -> str:
    """Ein waagerechtes Balkendiagramm als SVG-Markup.

    Gibt eine leere Zeichenkette zurück, wenn es nichts zu zeichnen gibt.
    """
    bars = [bar for bar in bars if bar.value > 0]
    if not bars:
        return ""

    ident = slugify(chart_id) or "diagramm"
    title_id = f"{ident}-titel"
    desc_id = f"{ident}-beschreibung"
    height = _TOP + len(bars) * _ROW_HEIGHT + _BOTTOM
    largest = max(bar.value for bar in bars)
    scale = _BAR_WIDTH / largest if largest > 0 else 0.0

    parts = [
        f'<svg class="balken" role="img" viewBox="0 0 {_WIDTH} {height}" '
        f'aria-labelledby="{title_id} {desc_id}" xmlns="http://www.w3.org/2000/svg">',
        f'<title id="{title_id}">{escape(title)}</title>',
        f'<desc id="{desc_id}">{escape(description)}</desc>',
        # Die Grundlinie, an der die Balken beginnen.
        f'<line x1="{_LABEL_WIDTH}" y1="{_TOP - 4}" x2="{_LABEL_WIDTH}" '
        f'y2="{height - _BOTTOM + 4}" class="achse" />',
    ]

    for index, bar in enumerate(bars):
        top = _TOP + index * _ROW_HEIGHT
        middle = top + _ROW_HEIGHT / 2
        # Mindestens ein schmaler Streifen, damit auch kleine Werte sichtbar sind.
        length = max(round(bar.value * scale, 1), 2)
        label = Truncator(bar.label).chars(MAX_LABEL_CHARS)
        parts.append(
            f"<g><title>{escape(bar.label)}</title>"
            f'<text x="{_LABEL_WIDTH - 10}" y="{middle}" text-anchor="end" '
            f'dominant-baseline="middle" class="beschriftung">{escape(label)}</text>'
            f'<rect x="{_LABEL_WIDTH}" y="{middle - _BAR_HEIGHT / 2}" width="{length}" '
            f'height="{_BAR_HEIGHT}" class="balken-flaeche" />'
            f'<text x="{_LABEL_WIDTH + length + 8}" y="{middle}" '
            f'dominant-baseline="middle" class="wert">{escape(bar.text)}</text></g>'
        )

    parts.append("</svg>")
    return mark_safe("".join(parts))  # noqa: S308 - jeder Fremdtext ist oben maskiert
