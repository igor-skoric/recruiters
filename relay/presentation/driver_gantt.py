"""Presentation helpers for driver assignment Gantt timeline.

Builds layout percentages and display labels from existing assignments
and status periods. Does not query the database or change state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

from relay.models import AssignmentStatus, DriverPeriodStatus, DriverStatusPeriod, RelayAssignment
from relay.presentation.truck_gantt import (
    DAY_LABELS,
    ENDS_SOON_DAYS,
    GanttDayHeader,
    GanttHandoff,
    GanttWeekBand,
    _bar_class,
    _pct,
)
from relay.services.relay_service import format_cycle_duration
from relay.services.timeline import WeekHeader


STATUS_KIND = {
    DriverPeriodStatus.OTR: "otr",
    DriverPeriodStatus.HOME_TIME: "home",
    DriverPeriodStatus.AVAILABLE: "available",
    DriverPeriodStatus.VACATION: "vacation",
    DriverPeriodStatus.UNAVAILABLE: "inactive",
    DriverPeriodStatus.INACTIVE: "inactive",
}


@dataclass
class DriverGanttBar:
    assignment: RelayAssignment
    truck_label: str
    status: str
    status_label: str
    start_date: date
    end_date: date
    display_end: date
    duration_label: str
    left_pct: float
    width_pct: float
    bar_class: str
    ends_soon: bool
    tooltip_lines: list[str] = field(default_factory=list)


@dataclass
class DriverStatusSegment:
    start: date
    end: date
    left_pct: float
    width_pct: float
    label: str
    kind: str
    tooltip: str


@dataclass
class DriverGanttTimeline:
    period_start: date
    period_end: date
    period_label: str
    total_days: int
    day_headers: list[GanttDayHeader]
    week_bands: list[GanttWeekBand]
    bars: list[DriverGanttBar]
    status_segments: list[DriverStatusSegment]
    handoffs: list[GanttHandoff]
    today_left_pct: float | None
    has_content: bool


def _tooltip_lines(assignment: RelayAssignment, duration_label: str) -> list[str]:
    lines = [
        f"Truck: {assignment.truck.unit_number}",
        f"Status: {assignment.get_status_display()}",
        f"Start: {assignment.start_date:%b %d, %Y}",
        f"Home time: {assignment.expected_end_date:%b %d, %Y}",
    ]
    if assignment.actual_end_date:
        lines.append(f"Actual end: {assignment.actual_end_date:%b %d, %Y}")
    if duration_label:
        lines.append(f"Duration: {duration_label}")
    notes = (assignment.notes or "").strip()
    if notes:
        lines.append(f"Notes: {notes[:160]}")
    return lines


def _period_exclusive_end(period: DriverStatusPeriod, horizon_end: date) -> date:
    return period.end_date or horizon_end


def build_driver_gantt(
    assignments: list[RelayAssignment],
    status_periods: list[DriverStatusPeriod],
    week_headers: list[WeekHeader],
    *,
    today: date | None = None,
) -> DriverGanttTimeline:
    """Build day-based Gantt presentation data for a driver detail page."""
    today = today or timezone.localdate()
    if not week_headers:
        monday = today - timedelta(days=today.weekday())
        period_start = monday
        period_end = monday + timedelta(weeks=8)
    else:
        period_start = week_headers[0].week_start
        period_end = week_headers[-1].week_end

    total_days = max((period_end - period_start).days, 1)
    period_label = (
        f"{period_start:%b %d} – {(period_end - timedelta(days=1)):%b %d, %Y}"
    )

    day_headers: list[GanttDayHeader] = []
    cursor = period_start
    while cursor < period_end:
        day_headers.append(
            GanttDayHeader(
                day=cursor,
                day_num=cursor.day,
                weekday=DAY_LABELS[cursor.weekday()],
                is_today=cursor == today,
                is_weekend=cursor.weekday() >= 5,
            )
        )
        cursor += timedelta(days=1)

    week_bands: list[GanttWeekBand] = []
    for header in week_headers:
        left = _pct(header.week_start, period_start, total_days)
        right = _pct(min(header.week_end, period_end), period_start, total_days)
        week_bands.append(
            GanttWeekBand(
                label=header.label,
                week_start=header.week_start,
                left_pct=left,
                width_pct=max(right - left, 0),
            )
        )

    visible = [
        a
        for a in assignments
        if a.status != AssignmentStatus.CANCELLED
        and a.start_date < period_end
        and a.effective_end_date > period_start
    ]
    visible.sort(key=lambda a: (a.start_date, a.pk or 0))

    bars: list[DriverGanttBar] = []
    for assignment in visible:
        bar_start = max(assignment.start_date, period_start)
        bar_end = min(assignment.effective_end_date, period_end)
        if bar_end <= bar_start:
            continue
        left = _pct(bar_start, period_start, total_days)
        right = _pct(bar_end, period_start, total_days)
        duration_label = format_cycle_duration(
            assignment.start_date,
            assignment.expected_end_date,
        )
        ends_soon = False
        if assignment.status == AssignmentStatus.ACTIVE:
            days_left = (assignment.expected_end_date - today).days
            ends_soon = 0 <= days_left <= ENDS_SOON_DAYS

        bars.append(
            DriverGanttBar(
                assignment=assignment,
                truck_label=assignment.truck.unit_number,
                status=assignment.status,
                status_label=assignment.get_status_display(),
                start_date=assignment.start_date,
                end_date=assignment.effective_end_date,
                display_end=assignment.expected_end_date,
                duration_label=duration_label,
                left_pct=left,
                width_pct=max(right - left, 0.4),
                bar_class=_bar_class(assignment.status, ends_soon),
                ends_soon=ends_soon,
                tooltip_lines=_tooltip_lines(assignment, duration_label),
            )
        )

    handoffs: list[GanttHandoff] = []
    for index, assignment in enumerate(visible):
        if index + 1 >= len(visible):
            break
        nxt = visible[index + 1]
        end = assignment.effective_end_date
        start = nxt.start_date
        if end == start and period_start <= end < period_end:
            handoffs.append(
                GanttHandoff(
                    day=end,
                    left_pct=_pct(end, period_start, total_days),
                    label=(
                        f"Truck change on {end:%b %d, %Y}: "
                        f"{assignment.truck.unit_number} → {nxt.truck.unit_number}"
                    ),
                )
            )

    status_segments: list[DriverStatusSegment] = []
    for period in status_periods:
        p_start = period.start_date
        p_end = _period_exclusive_end(period, period_end)
        seg_start = max(p_start, period_start)
        seg_end = min(p_end, period_end)
        if seg_end <= seg_start:
            continue
        kind = STATUS_KIND.get(period.status, "available")
        label = period.get_status_display()
        truck_bit = ""
        if period.assignment_id and getattr(period, "assignment", None):
            truck_bit = f" · Truck {period.assignment.truck.unit_number}"
        status_segments.append(
            DriverStatusSegment(
                start=seg_start,
                end=seg_end,
                left_pct=_pct(seg_start, period_start, total_days),
                width_pct=max(
                    _pct(seg_end, period_start, total_days)
                    - _pct(seg_start, period_start, total_days),
                    0.4,
                ),
                label=label,
                kind=kind,
                tooltip=(
                    f"{label}: {seg_start:%b %d} → "
                    f"{(seg_end - timedelta(days=1)):%b %d}{truck_bit}"
                ),
            )
        )

    today_left_pct = None
    if period_start <= today < period_end:
        today_left_pct = _pct(today, period_start, total_days) + (100 / total_days) / 2

    return DriverGanttTimeline(
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        total_days=total_days,
        day_headers=day_headers,
        week_bands=week_bands,
        bars=bars,
        status_segments=status_segments,
        handoffs=handoffs,
        today_left_pct=today_left_pct,
        has_content=bool(bars or status_segments),
    )
