"""Presentation helpers for truck assignment Gantt timeline.

Builds layout percentages and display labels from existing assignment
objects and week headers. Does not query the database or change assignment
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

from relay.models import AssignmentStatus, RelayAssignment
from relay.services.relay_service import format_cycle_duration
from relay.services.timeline import WeekHeader


DAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ENDS_SOON_DAYS = 14


@dataclass
class GanttDayHeader:
    day: date
    day_num: int
    weekday: str
    is_today: bool
    is_weekend: bool


@dataclass
class GanttWeekBand:
    label: str
    week_start: date
    left_pct: float
    width_pct: float

    @property
    def display_end(self) -> date:
        """Inclusive Sunday for week-band labels only."""
        return self.week_start + timedelta(days=6)


@dataclass
class GanttBar:
    assignment: RelayAssignment
    driver_name: str
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
class GanttHandoff:
    day: date
    left_pct: float
    label: str


@dataclass
class GanttGap:
    start: date
    end: date
    left_pct: float
    width_pct: float
    label: str
    kind: str  # available | needs_planning


@dataclass
class GanttTimeline:
    period_start: date
    period_end: date
    period_label: str
    total_days: int
    day_headers: list[GanttDayHeader]
    week_bands: list[GanttWeekBand]
    bars: list[GanttBar]
    handoffs: list[GanttHandoff]
    gaps: list[GanttGap]
    today_left_pct: float | None
    has_assignments: bool


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _pct(day: date, period_start: date, total_days: int) -> float:
    if total_days <= 0:
        return 0.0
    return _clamp(((day - period_start).days / total_days) * 100)


def _bar_class(status: str, ends_soon: bool) -> str:
    mapping = {
        AssignmentStatus.ACTIVE: "gantt-bar-active",
        AssignmentStatus.PLANNED: "gantt-bar-planned",
        AssignmentStatus.COMPLETED: "gantt-bar-completed",
        AssignmentStatus.CANCELLED: "gantt-bar-cancelled",
    }
    base = mapping.get(status, "gantt-bar-completed")
    if ends_soon and status == AssignmentStatus.ACTIVE:
        return f"{base} gantt-bar-ends-soon"
    return base


def _tooltip_lines(assignment: RelayAssignment, duration_label: str) -> list[str]:
    lines = [
        f"Driver: {assignment.driver.full_name}",
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


def build_truck_gantt(
    assignments: list[RelayAssignment],
    week_headers: list[WeekHeader],
    *,
    today: date | None = None,
) -> GanttTimeline:
    """Build Gantt presentation data for the visible week period."""
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
    # Keep cancelled that intersect for dashed display if present in history window.
    cancelled_visible = [
        a
        for a in assignments
        if a.status == AssignmentStatus.CANCELLED
        and a.start_date < period_end
        and a.effective_end_date > period_start
    ]
    row_source = visible + cancelled_visible
    row_source.sort(key=lambda a: (a.start_date, a.pk or 0))

    bars: list[GanttBar] = []
    for assignment in row_source:
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
            GanttBar(
                assignment=assignment,
                driver_name=assignment.driver.full_name,
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

    # Handoffs and gaps from non-cancelled assignments intersecting/adjacent in period.
    schedule = sorted(visible, key=lambda a: (a.start_date, a.pk or 0))
    handoffs: list[GanttHandoff] = []
    gaps: list[GanttGap] = []

    for index, assignment in enumerate(schedule):
        if index + 1 >= len(schedule):
            break
        nxt = schedule[index + 1]
        end = assignment.effective_end_date
        start = nxt.start_date
        if end == start and period_start <= end < period_end:
            handoffs.append(
                GanttHandoff(
                    day=end,
                    left_pct=_pct(end, period_start, total_days),
                    label=f"Driver handoff on {end:%b %d, %Y}",
                )
            )
        elif end < start:
            gap_start = max(end, period_start)
            gap_end = min(start, period_end)
            if gap_end > gap_start:
                kind = "needs_planning" if gap_start >= today else "available"
                gaps.append(
                    GanttGap(
                        start=gap_start,
                        end=gap_end,
                        left_pct=_pct(gap_start, period_start, total_days),
                        width_pct=max(
                            _pct(gap_end, period_start, total_days)
                            - _pct(gap_start, period_start, total_days),
                            0.4,
                        ),
                        label="Needs Planning" if kind == "needs_planning" else "Available",
                        kind=kind,
                    )
                )

    # Leading / trailing open space inside the visible period.
    if schedule:
        first_start = schedule[0].start_date
        if period_start < first_start:
            gap_end = min(first_start, period_end)
            if gap_end > period_start:
                kind = "needs_planning" if period_start >= today else "available"
                gaps.insert(
                    0,
                    GanttGap(
                        start=period_start,
                        end=gap_end,
                        left_pct=0.0,
                        width_pct=max(_pct(gap_end, period_start, total_days), 0.4),
                        label="Needs Planning" if kind == "needs_planning" else "Available",
                        kind=kind,
                    ),
                )
        last_end = schedule[-1].effective_end_date
        if last_end < period_end:
            gap_start = max(last_end, period_start)
            if period_end > gap_start:
                kind = "needs_planning" if gap_start >= today else "available"
                gaps.append(
                    GanttGap(
                        start=gap_start,
                        end=period_end,
                        left_pct=_pct(gap_start, period_start, total_days),
                        width_pct=max(
                            100 - _pct(gap_start, period_start, total_days),
                            0.4,
                        ),
                        label="Needs Planning" if kind == "needs_planning" else "Available",
                        kind=kind,
                    ),
                )
    elif period_end > period_start:
        kind = "needs_planning"
        gaps.append(
            GanttGap(
                start=period_start,
                end=period_end,
                left_pct=0.0,
                width_pct=100.0,
                label="Needs Planning",
                kind=kind,
            )
        )

    today_left_pct = None
    if period_start <= today < period_end:
        # Center the today marker in the day column.
        today_left_pct = _pct(today, period_start, total_days) + (100 / total_days) / 2

    return GanttTimeline(
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        total_days=total_days,
        day_headers=day_headers,
        week_bands=week_bands,
        bars=bars,
        handoffs=handoffs,
        gaps=gaps,
        today_left_pct=today_left_pct,
        has_assignments=bool(bars),
    )
