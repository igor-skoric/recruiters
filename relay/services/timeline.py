"""Weekly timeline computed from RelayAssignment / DriverStatusPeriod (no stored colors)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

from relay.models import AssignmentStatus, DriverPeriodStatus, RelayAssignment
from trucks.models import Truck, TruckStatus


class TruckWeekStatus:
    OCCUPIED = "otr"  # red
    AVAILABLE = "home"  # green (reuse CSS class)
    NEEDS_PLANNING = "review"  # yellow
    UNAVAILABLE = "inactive"  # gray


class DriverWeekStatus:
    OTR = "otr"
    HOME_TIME = "home"
    AVAILABLE = "available"
    VACATION = "vacation"
    UNAVAILABLE = "inactive"
    GAP = "review"


@dataclass
class WeekHeader:
    week_number: int
    label: str
    week_start: date  # Monday inclusive
    week_end: date  # next Monday exclusive — half-open [start, end)
    is_current: bool = False
    iso_year: int = 0

    @property
    def display_end(self) -> date:
        """Inclusive Sunday for labels."""
        return self.week_end - timedelta(days=1)


@dataclass
class AssignmentWeekHit:
    assignment: RelayAssignment
    driver_name: str
    start_date: date
    end_date: date
    status: str


@dataclass
class WeekCircle:
    week_number: int
    status: str
    status_label: str
    tooltip: str
    is_current: bool = False
    assignments: list[AssignmentWeekHit] = field(default_factory=list)


def periods_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    return start_a < end_b and start_b < end_a


def monday_of(value: date) -> date:
    return value - timedelta(days=value.weekday())


def get_board_week_headers(
    week_count: int = 12,
    *,
    start_date: date | None = None,
    today: date | None = None,
) -> list[WeekHeader]:
    """ISO weeks as half-open [monday, next_monday)."""
    today = today or timezone.localdate()
    if start_date is None:
        start_monday = monday_of(today)
    else:
        start_monday = monday_of(start_date)

    headers: list[WeekHeader] = []
    for index in range(week_count):
        week_start = start_monday + timedelta(weeks=index)
        week_end = week_start + timedelta(weeks=1)
        iso_year, iso_week, _ = week_start.isocalendar()
        is_current = week_start <= today < week_end
        headers.append(
            WeekHeader(
                week_number=iso_week,
                label=f"W{iso_week}",
                week_start=week_start,
                week_end=week_end,
                is_current=is_current,
                iso_year=iso_year,
            )
        )
    return headers


def assignment_intersects_week(assignment: RelayAssignment, week_start: date, week_end: date) -> bool:
    return periods_overlap(
        assignment.start_date,
        assignment.effective_end_date,
        week_start,
        week_end,
    )


def _status_label(status: str) -> str:
    return {
        TruckWeekStatus.OCCUPIED: "Occupied",
        TruckWeekStatus.AVAILABLE: "Available",
        TruckWeekStatus.NEEDS_PLANNING: "Needs Planning",
        TruckWeekStatus.UNAVAILABLE: "Unavailable",
    }.get(status, status)


def build_truck_week_tooltip(
    header: WeekHeader,
    status: str,
    hits: list[AssignmentWeekHit],
    *,
    needs_next: bool = False,
    truck_status: str | None = None,
    notes: str = "",
) -> str:
    date_range = f"{header.week_start:%b %d}–{header.display_end:%b %d, %Y}"
    parts = [
        f"W{header.week_number} ({header.iso_year})",
        date_range,
        _status_label(status),
    ]

    if hits:
        if len(hits) == 1:
            hit = hits[0]
            parts.append(f"Driver: {hit.driver_name}")
            parts.append(f"{hit.start_date:%b %d} → {hit.end_date:%b %d}")
            parts.append(hit.status.upper())
        else:
            ordered = sorted(hits, key=lambda h: h.start_date)
            change = ordered[1].start_date
            parts.append(
                f"Handoff {change:%b %d}: "
                + " → ".join(f"{h.driver_name} ({h.status})" for h in ordered)
            )
            for hit in ordered:
                parts.append(
                    f"{hit.driver_name}: {hit.start_date:%b %d}–{hit.end_date:%b %d} [{hit.status}]"
                )

    if needs_next:
        parts.append("Needs next driver")

    if status == TruckWeekStatus.UNAVAILABLE and truck_status:
        parts.append(f"Truck status: {truck_status}")
        if notes:
            parts.append(notes[:120])

    if header.is_current:
        parts.append("This week")

    return " | ".join(parts)


def resolve_truck_week_circle(
    truck: Truck,
    assignments: list[RelayAssignment],
    header: WeekHeader,
    *,
    today: date | None = None,
) -> WeekCircle:
    today = today or timezone.localdate()
    week_start, week_end = header.week_start, header.week_end

    if truck.status in {TruckStatus.INACTIVE, TruckStatus.MAINTENANCE}:
        tooltip = build_truck_week_tooltip(
            header,
            TruckWeekStatus.UNAVAILABLE,
            [],
            truck_status=truck.get_status_display(),
            notes=truck.notes or "",
        )
        return WeekCircle(
            week_number=header.week_number,
            status=TruckWeekStatus.UNAVAILABLE,
            status_label=_status_label(TruckWeekStatus.UNAVAILABLE),
            tooltip=tooltip,
            is_current=header.is_current,
        )

    hits: list[AssignmentWeekHit] = []
    for assignment in assignments:
        if assignment.status == AssignmentStatus.CANCELLED:
            continue
        if assignment_intersects_week(assignment, week_start, week_end):
            hits.append(
                AssignmentWeekHit(
                    assignment=assignment,
                    driver_name=assignment.driver.full_name,
                    start_date=assignment.start_date,
                    end_date=assignment.effective_end_date,
                    status=assignment.status,
                )
            )

    if hits:
        tooltip = build_truck_week_tooltip(header, TruckWeekStatus.OCCUPIED, hits)
        return WeekCircle(
            week_number=header.week_number,
            status=TruckWeekStatus.OCCUPIED,
            status_label=_status_label(TruckWeekStatus.OCCUPIED),
            tooltip=tooltip,
            is_current=header.is_current,
            assignments=hits,
        )

    prior = [a for a in assignments if a.effective_end_date <= week_start]
    upcoming = [a for a in assignments if a.start_date >= week_start]
    last_end = max((a.effective_end_date for a in prior), default=None)
    next_start = min((a.start_date for a in upcoming), default=None)

    in_open_gap = last_end is not None and (
        next_start is None or week_start < next_start
    ) and week_start >= last_end

    needs_next = in_open_gap and next_start is None
    if needs_next and week_end > today:
        status = TruckWeekStatus.NEEDS_PLANNING
    else:
        status = TruckWeekStatus.AVAILABLE

    tooltip = build_truck_week_tooltip(
        header,
        status,
        [],
        needs_next=needs_next,
    )
    return WeekCircle(
        week_number=header.week_number,
        status=status,
        status_label=_status_label(status),
        tooltip=tooltip,
        is_current=header.is_current,
    )


def build_truck_week_circles(
    truck: Truck,
    assignments: list[RelayAssignment],
    week_headers: list[WeekHeader],
    *,
    today: date | None = None,
) -> list[WeekCircle]:
    return [
        resolve_truck_week_circle(truck, assignments, header, today=today)
        for header in week_headers
    ]


def _period_bounds(start_date: date, end_date: date | None, fallback_end: date) -> tuple[date, date]:
    return start_date, end_date or fallback_end


def resolve_driver_week_circle(
    status_periods,
    assignments: list[RelayAssignment],
    header: WeekHeader,
    *,
    horizon_end: date,
) -> WeekCircle:
    week_start, week_end = header.week_start, header.week_end

    for period in status_periods:
        p_start, p_end = _period_bounds(period.start_date, period.end_date, horizon_end)
        if not periods_overlap(p_start, p_end, week_start, week_end):
            continue
        status_map = {
            DriverPeriodStatus.OTR: (DriverWeekStatus.OTR, "OTR"),
            DriverPeriodStatus.HOME_TIME: (DriverWeekStatus.HOME_TIME, "Home time"),
            DriverPeriodStatus.AVAILABLE: (DriverWeekStatus.AVAILABLE, "Available"),
            DriverPeriodStatus.VACATION: (DriverWeekStatus.VACATION, "Vacation"),
            DriverPeriodStatus.UNAVAILABLE: (DriverWeekStatus.UNAVAILABLE, "Unavailable"),
            DriverPeriodStatus.INACTIVE: (DriverWeekStatus.UNAVAILABLE, "Inactive"),
        }
        status, label = status_map.get(period.status, (DriverWeekStatus.GAP, period.status))
        truck_bit = ""
        if period.assignment_id and period.assignment:
            truck_bit = f" | Truck {period.assignment.truck.unit_number}"
        tooltip = (
            f"W{header.week_number} | {header.week_start:%b %d}–{header.display_end:%b %d} | "
            f"{label}{truck_bit}"
        )
        return WeekCircle(
            week_number=header.week_number,
            status=status,
            status_label=label,
            tooltip=tooltip,
            is_current=header.is_current,
        )

    for assignment in assignments:
        if assignment.status == AssignmentStatus.CANCELLED:
            continue
        if assignment_intersects_week(assignment, week_start, week_end):
            tooltip = (
                f"W{header.week_number} | OTR | Truck {assignment.truck.unit_number} | "
                f"{assignment.start_date:%b %d}→{assignment.effective_end_date:%b %d} | "
                f"{assignment.status}"
            )
            return WeekCircle(
                week_number=header.week_number,
                status=DriverWeekStatus.OTR,
                status_label="OTR",
                tooltip=tooltip,
                is_current=header.is_current,
            )

    tooltip = (
        f"W{header.week_number} | {header.week_start:%b %d}–{header.display_end:%b %d} | "
        f"Gap / unknown"
    )
    return WeekCircle(
        week_number=header.week_number,
        status=DriverWeekStatus.GAP,
        status_label="Gap",
        tooltip=tooltip,
        is_current=header.is_current,
    )


def build_driver_week_circles(
    status_periods,
    assignments: list[RelayAssignment],
    week_headers: list[WeekHeader],
) -> list[WeekCircle]:
    horizon_end = week_headers[-1].week_end if week_headers else timezone.localdate()
    return [
        resolve_driver_week_circle(status_periods, assignments, header, horizon_end=horizon_end)
        for header in week_headers
    ]
