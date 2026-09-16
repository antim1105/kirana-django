"""
Chart geometry, replacing `components/charts/PriceChart.tsx` and `TrendBars.tsx`.

The original drew SVG by hand in React rather than pull in a charting
library. The same idea works better server-side: this module turns data into
coordinates, and the template renders the SVG. No JavaScript, and the chart
prints correctly because it is just markup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from . import calc
from .analytics import MonthPoint, PricePoint, month_label

ZERO = Decimal("0")


@dataclass
class ChartPoint:
    x: float
    y: float
    label: str
    value: Decimal
    hint: str = ""


@dataclass
class LineChart:
    width: int = 720
    height: int = 260
    padding_left: int = 56
    padding_right: int = 16
    padding_top: int = 16
    padding_bottom: int = 34
    points: list[ChartPoint] = field(default_factory=list)
    effective_points: list[ChartPoint] = field(default_factory=list)
    grid_lines: list[dict] = field(default_factory=list)
    x_labels: list[dict] = field(default_factory=list)
    low: Decimal = ZERO
    high: Decimal = ZERO

    @property
    def has_data(self) -> bool:
        return len(self.points) > 0

    @property
    def polyline(self) -> str:
        return " ".join(f"{p.x:.1f},{p.y:.1f}" for p in self.points)

    @property
    def effective_polyline(self) -> str:
        return " ".join(f"{p.x:.1f},{p.y:.1f}" for p in self.effective_points)

    @property
    def area_path(self) -> str:
        """The polyline closed down to the baseline, for the soft fill."""
        if not self.points:
            return ""
        baseline = self.height - self.padding_bottom
        first, last = self.points[0], self.points[-1]
        segments = [f"M {first.x:.1f} {baseline:.1f}"]
        segments += [f"L {p.x:.1f} {p.y:.1f}" for p in self.points]
        segments.append(f"L {last.x:.1f} {baseline:.1f} Z")
        return " ".join(segments)


def price_chart(series: Sequence[PricePoint], *, width: int = 720, height: int = 260) -> LineChart:
    """
    A price line for one product, with the effective rate as a second line.

    A single data point would have nothing to draw a line between, so it is
    plotted as a dot in the middle of the canvas instead.
    """
    chart = LineChart(width=width, height=height)
    if not series:
        return chart

    rates = [calc.to_decimal(point.rate) for point in series]
    effectives = [calc.to_decimal(point.effective_rate) for point in series]
    low = min(rates + effectives)
    high = max(rates + effectives)

    # A flat series would divide by zero, so open a small window around it.
    if high == low:
        pad = high * Decimal("0.05") if high else Decimal("1")
        low, high = low - pad, high + pad
    else:
        headroom = (high - low) * Decimal("0.12")
        low, high = low - headroom, high + headroom

    chart.low, chart.high = calc.round2(low), calc.round2(high)

    plot_left = chart.padding_left
    plot_right = width - chart.padding_right
    plot_top = chart.padding_top
    plot_bottom = height - chart.padding_bottom
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    span = high - low

    def y_for(value: Decimal) -> float:
        ratio = (calc.to_decimal(value) - low) / span
        return float(plot_bottom - ratio * Decimal(plot_height))

    def x_for(index: int) -> float:
        if len(series) == 1:
            return plot_left + plot_width / 2
        return plot_left + plot_width * index / (len(series) - 1)

    for index, point in enumerate(series):
        chart.points.append(
            ChartPoint(
                x=x_for(index),
                y=y_for(point.rate),
                label=point.date.strftime("%d %b"),
                value=calc.to_decimal(point.rate),
                hint=point.wholesaler_name,
            )
        )
        chart.effective_points.append(
            ChartPoint(
                x=x_for(index),
                y=y_for(point.effective_rate),
                label=point.date.strftime("%d %b"),
                value=calc.to_decimal(point.effective_rate),
                hint="Effective rate",
            )
        )

    # Four horizontal guides, labelled with the rate they sit at.
    steps = 4
    for step in range(steps + 1):
        value = low + span * Decimal(step) / Decimal(steps)
        chart.grid_lines.append(
            {
                "y": y_for(value),
                "value": calc.round2(value),
                "x1": plot_left,
                "x2": plot_right,
            }
        )

    # Thin out x labels so they never overlap on a narrow screen.
    stride = max(1, len(series) // 6)
    for index, point in enumerate(chart.points):
        if index % stride == 0 or index == len(chart.points) - 1:
            chart.x_labels.append({"x": point.x, "label": point.label, "y": plot_bottom + 20})

    return chart


@dataclass
class Bar:
    label: str
    value: Decimal
    percent: float


def trend_bars(points: Sequence[MonthPoint]) -> list[Bar]:
    """
    Monthly spend as proportional bars.

    Percentages are relative to the biggest month, which is what makes a
    six-month strip readable without an axis.
    """
    if not points:
        return []
    highest = max(calc.to_decimal(point.amount) for point in points)
    bars = []
    for point in points:
        amount = calc.to_decimal(point.amount)
        ratio = float(amount / highest * 100) if highest else 0.0
        bars.append(
            Bar(
                label=month_label(point.month).replace(" ", "\n", 1),
                value=amount,
                # Keep a sliver visible for a month with a tiny total.
                percent=max(ratio, 2.0) if amount else 0.0,
            )
        )
    return bars


@dataclass
class ShareRow:
    label: str
    value: Decimal
    percent: float
    detail: str = ""


def share_rows(rows: Sequence[tuple[str, Decimal, str]]) -> list[ShareRow]:
    """Turns (label, amount, detail) tuples into proportional rows."""
    amounts = [calc.to_decimal(amount) for _, amount, _ in rows]
    highest = max(amounts) if amounts else ZERO
    result = []
    for (label, amount, detail), value in zip(rows, amounts):
        ratio = float(value / highest * 100) if highest else 0.0
        result.append(
            ShareRow(label=label, value=value, percent=max(ratio, 2.0), detail=detail)
        )
    return result
