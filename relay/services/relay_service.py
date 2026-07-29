from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from drivers.models import Driver, DriverStatus
from relay.models import (
    AssignmentStatus,
    DriverPeriodStatus,
    DriverStatusPeriod,
    RelayAssignment,
    RelayStatusOverride,
    ReviewStatus,
)
from trucks.models import Truck, TruckStatus
from relay.services.timeline import (
    WeekCircle,
    WeekHeader,
    build_driver_week_circles,
    build_truck_week_circles,
    get_board_week_headers as build_week_headers,
    resolve_truck_day_status,
    DAY_STATUS_FILTERS,
    TruckWeekStatus,
)

if TYPE_CHECKING:
    from accounts.models import User

DEFAULT_CYCLE_WEEKS = 4
DEFAULT_HOME_TIME_DAYS = 7
BOARD_WEEK_COUNT = 8
ALLOWED_WEEK_COUNTS = {5, 8, 12, 16}


class WeekCircleStatus:
    OTR = "otr"
    HOME = "home"
    REVIEW = "review"
    INACTIVE = "inactive"
    AVAILABLE = "available"


@dataclass
class RelayBoardRow:
    truck: Truck
    current_driver: Driver | None
    truck_status: str
    truck_status_label: str
    driver_status: str | None
    driver_status_label: str | None
    cycle_start_date: date | None
    expected_home_date: date | None
    days_left: int | None
    days_on_road: int | None
    next_driver: Driver | None
    next_driver_start_date: date | None
    review_status: str
    review_status_label: str
    notes: str
    override_id: int | None
    assignment_id: int | None
    needs_review: bool
    planning_status: str = "ok"
    planning_status_label: str = "OK"
    assignments: list | None = None

    @property
    def current_cycle_duration(self) -> str | None:
        if self.cycle_start_date and self.expected_home_date:
            return format_cycle_duration_compact(
                self.cycle_start_date, self.expected_home_date
            )
        return None


def get_board_week_headers(
    week_count: int = BOARD_WEEK_COUNT,
    *,
    start_date: date | None = None,
    today: date | None = None,
) -> list[WeekHeader]:
    return build_week_headers(week_count, start_date=start_date, today=today)


def get_truck_week_circles(
    row: RelayBoardRow,
    week_headers: list[WeekHeader] | None = None,
    *,
    assignments: list[RelayAssignment] | None = None,
) -> list[WeekCircle]:
    """Build weekly circles from real assignments (no 4+1 template)."""
    if week_headers is None:
        week_headers = get_board_week_headers()
    truck_assignments = assignments
    if truck_assignments is None:
        truck_assignments = row.assignments
    if truck_assignments is None:
        truck_assignments = list(
            RelayAssignment.objects.filter(truck=row.truck)
            .exclude(status=AssignmentStatus.CANCELLED)
            .select_related("driver")
            .order_by("start_date")
        )
    return build_truck_week_circles(row.truck, truck_assignments, week_headers)


def get_truck_day_status(
    row: RelayBoardRow,
    day: date,
    *,
    assignments: list[RelayAssignment] | None = None,
) -> dict:
    """Day occupancy status for fleet board date/status filters."""
    truck_assignments = assignments
    if truck_assignments is None:
        truck_assignments = row.assignments
    if truck_assignments is None:
        truck_assignments = list(
            RelayAssignment.objects.filter(truck=row.truck)
            .exclude(status=AssignmentStatus.CANCELLED)
            .select_related("driver")
            .order_by("start_date")
        )
    return resolve_truck_day_status(row.truck, truck_assignments, day)


def get_driver_week_circles(
    driver: Driver,
    week_headers: list[WeekHeader] | None = None,
) -> list[WeekCircle]:
    if week_headers is None:
        week_headers = get_board_week_headers()
    history = get_driver_history(driver)
    return build_driver_week_circles(
        history["status_periods"],
        history["assignments"],
        week_headers,
    )


def calculate_expected_end_date(start_date: date, cycle_weeks: int = DEFAULT_CYCLE_WEEKS) -> date:
    """Return exclusive end of OTR cycle (start of home time): start_date + cycle_weeks."""
    return start_date + timedelta(weeks=cycle_weeks)


def cycle_weeks_from_dates(start_date: date, home_time_date: date) -> int:
    """Derive stored cycle_weeks from start → home-time (exclusive end) dates."""
    days = (home_time_date - start_date).days
    if days < 1:
        raise ValidationError("Home time date must be after start date.")
    return max(1, (days + 6) // 7)


def format_cycle_duration(start_date: date, home_time_date: date) -> str:
    """Human-readable cycle length, e.g. '4 weeks', '3 weeks 5 days'."""
    days = (home_time_date - start_date).days
    if days < 1:
        return "—"
    weeks, rem = divmod(days, 7)
    parts: list[str] = []
    if weeks:
        parts.append(f"{weeks} week{'s' if weeks != 1 else ''}")
    if rem:
        parts.append(f"{rem} day{'s' if rem != 1 else ''}")
    return " ".join(parts) if parts else "0 days"


def format_cycle_duration_compact(start_date: date, home_time_date: date) -> str:
    """Compact cycle length for dense tables, e.g. '4w', '3w 5d'."""
    days = (home_time_date - start_date).days
    if days < 1:
        return "—"
    weeks, rem = divmod(days, 7)
    parts: list[str] = []
    if weeks:
        parts.append(f"{weeks}w")
    if rem:
        parts.append(f"{rem}d")
    return " ".join(parts) if parts else "0d"


def _week_monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _home_period_end(home_start: date, home_days: int = DEFAULT_HOME_TIME_DAYS) -> date:
    """Inclusive last day of home time starting on home_start."""
    return home_start + timedelta(days=home_days) - timedelta(days=1)


def _default_next_driver_start(expected_home_date: date | None) -> date | None:
    if not expected_home_date:
        return None
    return _home_period_end(expected_home_date) + timedelta(days=1)


def _home_time_end(assignment: RelayAssignment) -> date:
    """Exclusive end of home-time period after assignment ends."""
    end = assignment.actual_end_date or assignment.expected_end_date
    return end + timedelta(days=assignment.home_time_days)


def assignment_period_bounds(assignment: RelayAssignment) -> tuple[date, date]:
    """Half-open occupancy interval [start, end)."""
    return assignment.start_date, assignment.effective_end_date


def periods_overlap(start_a: date, end_a: date, start_b: date, end_b: date) -> bool:
    """True when half-open intervals [start_a, end_a) and [start_b, end_b) overlap."""
    return start_a < end_b and start_b < end_a


def validate_assignment_overlap(assignment: RelayAssignment) -> None:
    """
    Reject PLANNED/ACTIVE/COMPLETED periods that overlap on the same truck or driver.
    CANCELLED assignments are ignored. Intervals are [start_date, end_date).
    """
    if assignment.status == AssignmentStatus.CANCELLED:
        return
    if not assignment.start_date or not assignment.truck_id or not assignment.driver_id:
        return

    start, end = assignment_period_bounds(assignment)
    if end <= start:
        raise ValidationError("Assignment end date must be after start date.")

    candidates = (
        RelayAssignment.objects.exclude(status=AssignmentStatus.CANCELLED)
        .filter(
            models.Q(truck_id=assignment.truck_id) | models.Q(driver_id=assignment.driver_id)
        )
        .select_related("truck", "driver")
    )
    if assignment.pk:
        candidates = candidates.exclude(pk=assignment.pk)

    for other in candidates:
        other_start, other_end = assignment_period_bounds(other)
        if not periods_overlap(start, end, other_start, other_end):
            continue
        if other.truck_id == assignment.truck_id:
            raise ValidationError(
                {
                    "truck": (
                        f"Truck already has an assignment in this period "
                        f"({other.driver} {other_start}–{other_end})."
                    )
                }
            )
        if other.driver_id == assignment.driver_id:
            raise ValidationError(
                {
                    "driver": (
                        f"Driver already has an assignment in this period "
                        f"(truck {other.truck} {other_start}–{other_end})."
                    )
                }
            )


def get_current_assignment_for_truck(
    truck: Truck,
    as_of_date: date | None = None,
) -> RelayAssignment | None:
    as_of = as_of_date or timezone.localdate()
    for assignment in (
        RelayAssignment.objects.filter(truck=truck, status=AssignmentStatus.ACTIVE)
        .select_related("driver")
        .order_by("-start_date")
    ):
        start, end = assignment_period_bounds(assignment)
        if start <= as_of < end:
            return assignment
    return None


def get_current_assignment_for_driver(
    driver: Driver,
    as_of_date: date | None = None,
) -> RelayAssignment | None:
    as_of = as_of_date or timezone.localdate()
    for assignment in (
        RelayAssignment.objects.filter(driver=driver, status=AssignmentStatus.ACTIVE)
        .select_related("truck")
        .order_by("-start_date")
    ):
        start, end = assignment_period_bounds(assignment)
        if start <= as_of < end:
            return assignment
    return None


def get_next_assignment_for_truck(
    truck: Truck,
    as_of_date: date | None = None,
) -> RelayAssignment | None:
    as_of = as_of_date or timezone.localdate()
    current = get_current_assignment_for_truck(truck, as_of)
    qs = (
        RelayAssignment.objects.filter(truck=truck, status=AssignmentStatus.PLANNED)
        .select_related("driver")
        .order_by("start_date")
    )
    if current:
        qs = qs.filter(start_date__gte=current.effective_end_date)
    else:
        qs = qs.filter(start_date__gte=as_of)
    return qs.first()


def get_truck_history(truck: Truck) -> list[RelayAssignment]:
    return list(
        RelayAssignment.objects.filter(truck=truck)
        .exclude(status=AssignmentStatus.CANCELLED)
        .select_related("driver")
        .order_by("start_date", "id")
    )


def get_driver_history(driver: Driver) -> dict:
    assignments = list(
        RelayAssignment.objects.filter(driver=driver)
        .exclude(status=AssignmentStatus.CANCELLED)
        .select_related("truck")
        .order_by("start_date", "id")
    )
    status_periods = list(
        DriverStatusPeriod.objects.filter(driver=driver)
        .select_related("assignment", "assignment__truck")
        .order_by("start_date", "id")
    )
    return {
        "assignments": assignments,
        "status_periods": status_periods,
    }


def _ensure_otr_status_period(assignment: RelayAssignment) -> DriverStatusPeriod:
    period = (
        DriverStatusPeriod.objects.filter(
            assignment=assignment,
            status=DriverPeriodStatus.OTR,
        )
        .order_by("-id")
        .first()
    )
    end_date = assignment.expected_end_date
    if period:
        changed = False
        if period.start_date != assignment.start_date:
            period.start_date = assignment.start_date
            changed = True
        if period.end_date != end_date:
            period.end_date = end_date
            changed = True
        if changed:
            period.save(update_fields=["start_date", "end_date", "updated_at"])
        return period

    return DriverStatusPeriod.objects.create(
        driver=assignment.driver,
        status=DriverPeriodStatus.OTR,
        start_date=assignment.start_date,
        end_date=end_date,
        assignment=assignment,
    )


def _close_otr_status_period(assignment: RelayAssignment, actual_end_date: date) -> None:
    periods = DriverStatusPeriod.objects.filter(
        assignment=assignment,
        status=DriverPeriodStatus.OTR,
    )
    for period in periods:
        period.end_date = actual_end_date
        period.save(update_fields=["end_date", "updated_at"])


def _clear_otr_status_period(assignment: RelayAssignment) -> None:
    DriverStatusPeriod.objects.filter(
        assignment=assignment,
        status=DriverPeriodStatus.OTR,
    ).delete()


def _release_truck_from_assignment(assignment: RelayAssignment) -> None:
    """Clear truck cache when this assignment is no longer current occupancy."""
    truck = assignment.truck
    if truck.current_driver_id != assignment.driver_id:
        return
    if get_current_assignment_for_truck(truck) is not None:
        return
    truck.current_driver = None
    truck.status = TruckStatus.YARD
    truck.save(update_fields=["current_driver", "status", "updated_at"])


def _create_home_time_period(assignment: RelayAssignment, actual_end_date: date) -> DriverStatusPeriod:
    home_end = actual_end_date + timedelta(days=assignment.home_time_days)
    period = (
        DriverStatusPeriod.objects.filter(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        .order_by("-id")
        .first()
    )
    if period:
        period.start_date = actual_end_date
        period.end_date = home_end
        period.save(update_fields=["start_date", "end_date", "updated_at"])
        return period
    return DriverStatusPeriod.objects.create(
        driver=assignment.driver,
        status=DriverPeriodStatus.HOME_TIME,
        start_date=actual_end_date,
        end_date=home_end,
        assignment=assignment,
    )


def _sync_planned_home_time_period(assignment: RelayAssignment) -> DriverStatusPeriod | None:
    """Write planned home time to history before the driver actually goes home."""
    if assignment.status not in {AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED}:
        return None
    if not assignment.expected_end_date:
        return None
    return _create_home_time_period(assignment, assignment.expected_end_date)


def _clear_home_time_period_for_assignment(assignment: RelayAssignment) -> None:
    DriverStatusPeriod.objects.filter(
        assignment=assignment,
        status=DriverPeriodStatus.HOME_TIME,
    ).delete()


def _assignments_for_truck(truck: Truck) -> list[RelayAssignment]:
    prefetched = getattr(truck, "prefetched_assignments", None)
    if prefetched is not None:
        return list(prefetched)
    return list(
        RelayAssignment.objects.filter(truck=truck)
        .exclude(status=AssignmentStatus.CANCELLED)
        .select_related("driver")
        .order_by("start_date", "id")
    )


def _current_from_list(
    assignments: list[RelayAssignment],
    as_of: date,
) -> RelayAssignment | None:
    for assignment in assignments:
        if assignment.status != AssignmentStatus.ACTIVE:
            continue
        start, end = assignment_period_bounds(assignment)
        if start <= as_of < end:
            return assignment
    return None


def _next_from_list(
    assignments: list[RelayAssignment],
    as_of: date,
    current: RelayAssignment | None,
) -> RelayAssignment | None:
    planned = [a for a in assignments if a.status == AssignmentStatus.PLANNED]
    planned.sort(key=lambda a: (a.start_date, a.pk or 0))
    if current:
        for assignment in planned:
            if assignment.start_date >= current.effective_end_date:
                return assignment
        return None
    for assignment in planned:
        if assignment.start_date >= as_of:
            return assignment
    return None


def _sync_truck_and_driver_for_active(assignment: RelayAssignment) -> None:
    truck = assignment.truck
    driver = assignment.driver

    truck.current_driver = driver
    truck.status = TruckStatus.OTR
    truck.save(update_fields=["current_driver", "status", "updated_at"])

    driver.status = DriverStatus.ACTIVE
    driver.save(update_fields=["status", "updated_at"])

    _ensure_otr_status_period(assignment)
    _sync_planned_home_time_period(assignment)


def _resolve_board_data(truck: Truck) -> dict:
    """
    Merge occupancy for the board.

    Priority:
    1. ACTIVE / next PLANNED RelayAssignment (source of truth)
    2. RelayStatusOverride (legacy fallback only)
    Truck.current_driver is a derived cache and is NOT used as occupancy fallback.
    """
    override: RelayStatusOverride | None = getattr(truck, "status_override", None)
    today = timezone.localdate()
    assignments = _assignments_for_truck(truck)
    current_assignment = _current_from_list(assignments, today)
    next_assignment = _next_from_list(assignments, today, current_assignment)
    assignment = current_assignment or next(
        (a for a in reversed(assignments) if a.status in {
            AssignmentStatus.ACTIVE,
            AssignmentStatus.PLANNED,
        }),
        None,
    )

    current_driver = None
    truck_status = truck.status
    cycle_start_date = None
    expected_home_date = None
    next_driver = None
    next_driver_start_date = None

    if current_assignment:
        current_driver = current_assignment.driver
        truck_status = TruckStatus.OTR
        cycle_start_date = current_assignment.start_date
        expected_home_date = current_assignment.expected_end_date
    elif override and override.driver_id:
        current_driver = override.driver

    if next_assignment:
        next_driver = next_assignment.driver
        next_driver_start_date = next_assignment.start_date
    elif not current_assignment and override and override.next_driver_id:
        # Legacy fallback only when no assignment data exists.
        next_driver = override.next_driver
        next_driver_start_date = override.next_driver_start_date

    if cycle_start_date is None and not current_assignment and not next_assignment:
        if override and override.cycle_start_date:
            cycle_start_date = override.cycle_start_date

    if expected_home_date is None:
        if cycle_start_date and current_assignment:
            expected_home_date = current_assignment.expected_end_date
        elif cycle_start_date:
            expected_home_date = calculate_expected_end_date(cycle_start_date)
        elif assignment:
            expected_home_date = assignment.expected_end_date
        elif not current_assignment and not next_assignment and override and override.expected_home_date:
            expected_home_date = override.expected_home_date

    if next_driver and not next_driver_start_date:
        next_driver_start_date = _default_next_driver_start(expected_home_date)

    if (
        not current_assignment
        and not next_assignment
        and override
        and override.status_override
    ):
        truck_status = override.status_override

    # Manual workflow notes live on Truck / Assignment — not override.
    notes = truck.notes or ""

    planning_status = ReviewStatus.OK
    planning_label = "OK"
    if truck_status in {TruckStatus.MAINTENANCE, TruckStatus.INACTIVE}:
        planning_status = ReviewStatus.MAINTENANCE
        planning_label = "Unavailable"
    elif current_assignment and not next_assignment:
        days = (current_assignment.expected_end_date - today).days
        if days <= 14:
            planning_status = ReviewStatus.NEEDS_RELAY_PLANNING
            planning_label = "Needs Planning"
    elif not current_assignment and not next_assignment:
        if truck_status in {TruckStatus.YARD, TruckStatus.AVAILABLE}:
            planning_status = ReviewStatus.IN_YARD
            planning_label = "Available"
        else:
            planning_status = ReviewStatus.NEEDS_RELAY_PLANNING
            planning_label = "Needs Planning"

    return {
        "current_driver": current_driver,
        "truck_status": truck_status,
        "cycle_start_date": cycle_start_date,
        "expected_home_date": expected_home_date,
        "next_driver": next_driver,
        "next_driver_start_date": next_driver_start_date,
        "notes": notes,
        "override": override,
        "assignment": assignment,
        "current_assignment": current_assignment,
        "next_assignment": next_assignment,
        "assignments": assignments,
        "planning_status": planning_status,
        "planning_status_label": planning_label,
    }



def _compute_review_status(
    truck_status: str,
    current_driver: Driver | None,
    cycle_start_date: date | None,
    expected_home_date: date | None,
    next_driver: Driver | None,
    next_driver_start_date: date | None,
) -> str:
    if truck_status == TruckStatus.MAINTENANCE:
        return ReviewStatus.MAINTENANCE

    if not current_driver:
        if truck_status in {TruckStatus.YARD, TruckStatus.AVAILABLE}:
            return ReviewStatus.IN_YARD
        return ReviewStatus.MISSING_CURRENT_DRIVER

    if not cycle_start_date:
        return ReviewStatus.MISSING_CYCLE_START_DATE

    today = timezone.localdate()
    in_home_window = False
    if expected_home_date:
        home_end = _home_period_end(expected_home_date)
        in_home_window = expected_home_date <= today <= home_end

    if not next_driver and (
        in_home_window
        or (
            expected_home_date
            and 0 <= (expected_home_date - today).days <= 14
        )
    ):
        return ReviewStatus.NEEDS_RELAY_PLANNING

    if next_driver and not next_driver_start_date:
        return ReviewStatus.NEEDS_RELAY_PLANNING

    return ReviewStatus.OK


def _days_left(expected_home_date: date | None) -> int | None:
    if expected_home_date is None:
        return None
    return (expected_home_date - timezone.localdate()).days


def _days_on_road(cycle_start_date: date | None) -> int | None:
    """Days the current driver has already been on this assignment (from start to today)."""
    if cycle_start_date is None:
        return None
    today = timezone.localdate()
    if cycle_start_date > today:
        return None
    return (today - cycle_start_date).days


def _build_board_row(truck: Truck) -> RelayBoardRow:
    data = _resolve_board_data(truck)
    review_status = _compute_review_status(
        data["truck_status"],
        data["current_driver"],
        data["cycle_start_date"],
        data["expected_home_date"],
        data["next_driver"],
        data["next_driver_start_date"],
    )
    override = data["override"]
    assignment = data["assignment"]
    planning_status = data["planning_status"]
    if planning_status == ReviewStatus.NEEDS_RELAY_PLANNING:
        review_status = ReviewStatus.NEEDS_RELAY_PLANNING

    return RelayBoardRow(
        truck=truck,
        current_driver=data["current_driver"],
        truck_status=data["truck_status"],
        truck_status_label=dict(TruckStatus.choices).get(
            data["truck_status"], data["truck_status"]
        ),
        driver_status=data["current_driver"].status if data["current_driver"] else None,
        driver_status_label=(
            data["current_driver"].get_status_display() if data["current_driver"] else None
        ),
        cycle_start_date=data["cycle_start_date"],
        expected_home_date=data["expected_home_date"],
        days_left=_days_left(data["expected_home_date"]),
        days_on_road=_days_on_road(data["cycle_start_date"]),
        next_driver=data["next_driver"],
        next_driver_start_date=data["next_driver_start_date"],
        review_status=review_status,
        review_status_label=dict(ReviewStatus.choices).get(review_status, review_status),
        notes=data["notes"],
        override_id=override.pk if override else None,
        assignment_id=assignment.pk if assignment else None,
        needs_review=review_status != ReviewStatus.OK,
        planning_status=planning_status,
        planning_status_label=data["planning_status_label"],
        assignments=data["assignments"],
    )


@dataclass
class FleetSummary:
    total: int
    otr: int
    available: int
    needs_review: int
    no_driver: int
    maintenance: int


def get_fleet_summary(rows: list[RelayBoardRow]) -> FleetSummary:
    otr = available = needs_review = no_driver = maintenance = 0

    for row in rows:
        if row.truck_status in {TruckStatus.MAINTENANCE, TruckStatus.INACTIVE}:
            maintenance += 1
        elif row.review_status == ReviewStatus.IN_YARD:
            available += 1
        elif row.review_status == ReviewStatus.MISSING_CURRENT_DRIVER:
            no_driver += 1
        elif row.needs_review:
            needs_review += 1
        elif row.truck_status == TruckStatus.OTR:
            otr += 1
        elif row.truck_status in {TruckStatus.YARD, TruckStatus.AVAILABLE}:
            available += 1
        else:
            needs_review += 1

    return FleetSummary(
        total=len(rows),
        otr=otr,
        available=available,
        needs_review=needs_review,
        no_driver=no_driver,
        maintenance=maintenance,
    )


def get_truck_board_row(truck: Truck) -> RelayBoardRow:
    truck = (
        Truck.objects.select_related("current_driver", "status_override")
        .prefetch_related(
            models.Prefetch(
                "assignments",
                queryset=(
                    RelayAssignment.objects.exclude(status=AssignmentStatus.CANCELLED)
                    .select_related("driver")
                    .order_by("start_date", "id")
                ),
                to_attr="prefetched_assignments",
            ),
            "status_override__next_driver",
            "status_override__driver",
        )
        .get(pk=truck.pk)
    )
    return _build_board_row(truck)


def get_relay_board() -> list[RelayBoardRow]:
    """Build truck-centric fleet board with assignments prefetched (no N+1)."""
    trucks = (
        Truck.objects.select_related("current_driver", "status_override")
        .prefetch_related(
            models.Prefetch(
                "assignments",
                queryset=(
                    RelayAssignment.objects.exclude(status=AssignmentStatus.CANCELLED)
                    .select_related("driver")
                    .order_by("start_date", "id")
                ),
                to_attr="prefetched_assignments",
            ),
            "status_override__next_driver",
            "status_override__driver",
        )
        .order_by("unit_number")
    )
    return [_build_board_row(truck) for truck in trucks]


@transaction.atomic
def plan_next_assignment(
    truck: Truck,
    driver: Driver,
    start_date: date,
    *,
    cycle_weeks: int = DEFAULT_CYCLE_WEEKS,
    home_time_days: int = DEFAULT_HOME_TIME_DAYS,
    expected_end_date: date | None = None,
    notes: str = "",
    created_by: User | None = None,
    existing: RelayAssignment | None = None,
) -> RelayAssignment:
    """Create or update a PLANNED assignment. Does not write RelayStatusOverride."""
    if expected_end_date is None:
        expected_end = calculate_expected_end_date(start_date, cycle_weeks)
        weeks = cycle_weeks
    else:
        if expected_end_date <= start_date:
            raise ValidationError("Home time date must be after start date.")
        expected_end = expected_end_date
        weeks = cycle_weeks_from_dates(start_date, expected_end)

    if home_time_days < 1 or home_time_days > 60:
        raise ValidationError("Home time days must be between 1 and 60.")

    if existing is not None:
        if existing.status != AssignmentStatus.PLANNED:
            raise ValidationError("Only planned assignments can be edited here.")
        if existing.truck_id != truck.pk:
            raise ValidationError("Assignment does not belong to this truck.")
        driver_changed = existing.driver_id != driver.pk
        existing.driver = driver
        existing.start_date = start_date
        existing.expected_end_date = expected_end
        existing.cycle_weeks = weeks
        existing.home_time_days = home_time_days
        existing.notes = notes
        existing.actual_end_date = None
        existing.start_date_is_estimated = False
        validate_assignment_overlap(existing)
        existing.save()
        if driver_changed:
            # Old driver's planned home time no longer applies.
            _clear_home_time_period_for_assignment(existing)
        _sync_planned_home_time_period(existing)
        return existing

    return create_assignment(
        driver=driver,
        truck=truck,
        start_date=start_date,
        cycle_weeks=weeks,
        home_time_days=home_time_days,
        expected_end_date=expected_end,
        status=AssignmentStatus.PLANNED,
        notes=notes,
        created_by=created_by,
    )


@transaction.atomic
def update_assignment_home_time(
    assignment: RelayAssignment,
    home_time_date: date,
    *,
    start_date: date | None = None,
) -> RelayAssignment:
    """Update start and/or expected home/end date for an ACTIVE or PLANNED assignment.

    Truck occupancy is [start, expected_end), so the linked truck is free from
    home_time_date onward on the fleet timeline. When the new date is today or
    earlier for an ACTIVE assignment, process_relay_state() completes it so the
    truck is cleared operationally as well.

    If start is moved into the future, the assignment is demoted to PLANNED so it
    shows under Next (not as a phantom Active with empty Current).
    """
    if assignment.status not in {AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED}:
        raise ValidationError("Only active or planned assignments can change home time date.")

    today = timezone.localdate()
    new_start = start_date if start_date is not None else assignment.start_date
    if home_time_date <= new_start:
        raise ValidationError("Home time date must be after start date.")

    assignment.start_date = new_start
    assignment.expected_end_date = home_time_date
    assignment.cycle_weeks = cycle_weeks_from_dates(new_start, home_time_date)
    # Manual date confirmation/correction clears bootstrap estimated flag.
    assignment.start_date_is_estimated = False

    if new_start > today:
        # Not yet started — schedule only.
        demoted = assignment.status == AssignmentStatus.ACTIVE
        assignment.status = AssignmentStatus.PLANNED
        validate_assignment_overlap(assignment)
        assignment.save()
        _clear_otr_status_period(assignment)
        _sync_planned_home_time_period(assignment)
        if demoted:
            _release_truck_from_assignment(assignment)
        return assignment

    # Starts today or earlier — treat as live occupancy.
    validate_assignment_overlap(assignment)
    assignment.save()
    if assignment.status == AssignmentStatus.PLANNED:
        return activate_assignment(assignment)

    _ensure_otr_status_period(assignment)
    _sync_planned_home_time_period(assignment)
    return assignment


@transaction.atomic
def update_assignment_home_time_days(
    assignment: RelayAssignment,
    home_time_days: int,
) -> RelayAssignment:
    """Set how many days the driver stays home after this cycle ends."""
    if assignment.status not in {
        AssignmentStatus.ACTIVE,
        AssignmentStatus.PLANNED,
        AssignmentStatus.COMPLETED,
    }:
        raise ValidationError("Cannot change home time days for this assignment.")
    if home_time_days < 1 or home_time_days > 60:
        raise ValidationError("Home time days must be between 1 and 60.")

    assignment.home_time_days = home_time_days
    assignment.save(update_fields=["home_time_days", "updated_at"])

    if assignment.status == AssignmentStatus.COMPLETED and assignment.actual_end_date:
        period = (
            DriverStatusPeriod.objects.filter(
                assignment=assignment,
                status=DriverPeriodStatus.HOME_TIME,
            )
            .order_by("-id")
            .first()
        )
        if period:
            new_end = period.start_date + timedelta(days=home_time_days)
            update_driver_home_time_period(period, new_end)
    elif assignment.status in {AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED}:
        _sync_planned_home_time_period(assignment)

    return assignment


def apply_home_time_side_effects(assignment: RelayAssignment) -> None:
    """Run after update_assignment_home_time when date may require completion."""
    if (
        assignment.status == AssignmentStatus.ACTIVE
        and assignment.expected_end_date <= timezone.localdate()
    ):
        process_relay_state()


@transaction.atomic
def cancel_planned_assignment(assignment: RelayAssignment) -> RelayAssignment:
    Truck.objects.select_for_update().get(pk=assignment.truck_id)
    assignment = RelayAssignment.objects.select_for_update().get(pk=assignment.pk)
    if assignment.status != AssignmentStatus.PLANNED:
        raise ValidationError("Only planned assignments can be cancelled.")
    _clear_home_time_period_for_assignment(assignment)
    assignment.status = AssignmentStatus.CANCELLED
    assignment.save()
    return assignment


@dataclass
class ProcessRelayResult:
    activated: int = 0
    completed: int = 0
    yarded: int = 0
    home_time_cleared: int = 0
    demoted: int = 0


def _home_time_period_is_open(period: DriverStatusPeriod, as_of: date) -> bool:
    """True when as_of is inside [start_date, end_date)."""
    if period.start_date > as_of:
        return False
    end = period.end_date
    return end is None or as_of < end


def _driver_has_open_home_time(driver: Driver, as_of: date) -> bool:
    periods = DriverStatusPeriod.objects.filter(
        driver=driver,
        status=DriverPeriodStatus.HOME_TIME,
    )
    return any(_home_time_period_is_open(period, as_of) for period in periods)


def clear_expired_home_time_status(
    as_of_date: date | None = None,
) -> int:
    """
    Set drivers whose home time has ended back to Active (available, no truck).

    Idempotent. Skips drivers who still have an open HOME_TIME period or an
    ACTIVE assignment.
    """
    as_of = as_of_date or timezone.localdate()
    cleared = 0
    stuck = list(
        Driver.objects.filter(status=DriverStatus.HOME_TIME).order_by("id")
    )
    for driver in stuck:
        if _driver_has_open_home_time(driver, as_of):
            continue
        if get_current_assignment_for_driver(driver) is not None:
            continue
        driver.status = DriverStatus.ACTIVE
        driver.save(update_fields=["status", "updated_at"])
        cleared += 1
    return cleared


def demote_future_active_assignments(as_of_date: date | None = None) -> int:
    """ACTIVE with start_date in the future → PLANNED (not yet on the truck)."""
    as_of = as_of_date or timezone.localdate()
    demoted = 0
    future_active = list(
        RelayAssignment.objects.filter(
            status=AssignmentStatus.ACTIVE,
            start_date__gt=as_of,
        )
        .select_related("truck", "driver")
        .order_by("start_date", "id")
    )
    for assignment in future_active:
        assignment.status = AssignmentStatus.PLANNED
        assignment.save(update_fields=["status", "updated_at"])
        _clear_otr_status_period(assignment)
        _sync_planned_home_time_period(assignment)
        _release_truck_from_assignment(assignment)
        demoted += 1
    return demoted


@transaction.atomic
def process_relay_state(as_of_date: date | None = None) -> ProcessRelayResult:
    """
    Idempotent as-of-today processor for planned/active handoffs.

    Safe to run via management command / cron only (not from GET views).
    Does not create duplicate DriverStatusPeriod rows.

    Runs complete → activate in a short loop so a newly activated assignment
    that is already past its end is completed in the same pass.
    """
    as_of = as_of_date or timezone.localdate()
    result = ProcessRelayResult()

    # ACTIVE that hasn't started yet is a schedule, not current occupancy.
    result.demoted = demote_future_active_assignments(as_of)

    # Stabilize occupancy: complete due actives, activate due planned, repeat
    # briefly so catch-up dates (e.g. activate then already overdue) settle.
    for _ in range(5):
        progressed = False

        active_assignments = list(
            RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
            .select_related("truck", "driver")
            .order_by("start_date", "id")
        )
        for assignment in active_assignments:
            if as_of < assignment.effective_end_date:
                continue
            before_driver = assignment.truck.current_driver_id
            complete_assignment(
                assignment,
                actual_end_date=assignment.effective_end_date,
                as_of_date=as_of,
            )
            result.completed += 1
            progressed = True
            assignment.truck.refresh_from_db()
            if assignment.truck.current_driver_id is None:
                result.yarded += 1
            elif assignment.truck.current_driver_id != before_driver:
                result.activated += 1

        planned = list(
            RelayAssignment.objects.filter(
                status=AssignmentStatus.PLANNED,
                start_date__lte=as_of,
            )
            .select_related("truck", "driver")
            .order_by("start_date", "id")
        )
        for assignment in planned:
            other_active = (
                RelayAssignment.objects.filter(
                    truck_id=assignment.truck_id,
                    status=AssignmentStatus.ACTIVE,
                )
                .exclude(pk=assignment.pk)
                .exists()
            )
            if other_active:
                continue
            try:
                activate_assignment(assignment)
                result.activated += 1
                progressed = True
            except ValidationError:
                continue

        if not progressed:
            break

    result.home_time_cleared = clear_expired_home_time_status(as_of)
    return result


# --- Assignment lifecycle (source of truth) ---


@transaction.atomic
def activate_assignment(assignment: RelayAssignment) -> RelayAssignment:
    """
    Activate a planned (or already active) assignment.

    Refuses when another ACTIVE assignment exists on the same truck — complete
    that assignment first for a valid handoff.
    """
    Truck.objects.select_for_update().get(pk=assignment.truck_id)
    assignment = (
        RelayAssignment.objects.select_for_update()
        .select_related("truck", "driver")
        .get(pk=assignment.pk)
    )
    Driver.objects.select_for_update().get(pk=assignment.driver_id)

    other_active = (
        RelayAssignment.objects.filter(
            truck_id=assignment.truck_id,
            status=AssignmentStatus.ACTIVE,
        )
        .exclude(pk=assignment.pk)
        .exists()
    )
    if other_active:
        raise ValidationError(
            "Truck already has an active assignment. "
            "Complete it before activating another (handoff)."
        )

    driver_elsewhere = (
        RelayAssignment.objects.filter(
            driver_id=assignment.driver_id,
            status=AssignmentStatus.ACTIVE,
        )
        .exclude(pk=assignment.pk)
        .exists()
    )
    if driver_elsewhere:
        raise ValidationError(
            "Driver already has an active assignment on another truck."
        )

    if assignment.status == AssignmentStatus.CANCELLED:
        raise ValidationError("Cannot activate a cancelled assignment.")
    if assignment.status == AssignmentStatus.COMPLETED:
        raise ValidationError("Cannot activate a completed assignment.")

    assignment.status = AssignmentStatus.ACTIVE
    # Temporarily clear actual_end so occupancy uses expected_end for overlap check
    if assignment.actual_end_date:
        assignment.actual_end_date = None
    validate_assignment_overlap(assignment)
    assignment.save()

    _sync_truck_and_driver_for_active(assignment)
    return assignment


@transaction.atomic
def create_assignment(
    driver: Driver,
    truck: Truck,
    start_date: date,
    cycle_weeks: int = DEFAULT_CYCLE_WEEKS,
    home_time_days: int = DEFAULT_HOME_TIME_DAYS,
    created_by: User | None = None,
    status: str = AssignmentStatus.ACTIVE,
    notes: str = "",
    expected_end_date: date | None = None,
    start_date_is_estimated: bool = False,
) -> RelayAssignment:
    truck = Truck.objects.select_for_update().get(pk=truck.pk)
    driver = Driver.objects.select_for_update().get(pk=driver.pk)

    if expected_end_date is None:
        expected_end_date = calculate_expected_end_date(start_date, cycle_weeks)
    else:
        if expected_end_date <= start_date:
            raise ValidationError("Home time date must be after start date.")
        cycle_weeks = cycle_weeks_from_dates(start_date, expected_end_date)

    create_status = (
        AssignmentStatus.PLANNED
        if status == AssignmentStatus.ACTIVE
        else status
    )

    assignment = RelayAssignment(
        driver=driver,
        truck=truck,
        start_date=start_date,
        expected_end_date=expected_end_date,
        cycle_weeks=cycle_weeks,
        home_time_days=home_time_days,
        status=create_status,
        notes=notes,
        start_date_is_estimated=start_date_is_estimated,
        created_by=created_by,
    )
    validate_assignment_overlap(assignment)
    assignment.save()

    if status == AssignmentStatus.ACTIVE:
        if start_date > timezone.localdate():
            # Not yet started — keep Planned until process_relay_state / start day.
            _sync_planned_home_time_period(assignment)
            return assignment
        return activate_assignment(assignment)
    if create_status == AssignmentStatus.PLANNED:
        _sync_planned_home_time_period(assignment)
    return assignment


@transaction.atomic
def complete_assignment(
    assignment: RelayAssignment,
    actual_end_date: date | None = None,
    home_time_days: int | None = None,
    as_of_date: date | None = None,
) -> RelayAssignment:
    Truck.objects.select_for_update().get(pk=assignment.truck_id)
    assignment = (
        RelayAssignment.objects.select_for_update()
        .select_related("truck", "driver", "next_assignment")
        .get(pk=assignment.pk)
    )
    Driver.objects.select_for_update().get(pk=assignment.driver_id)

    if assignment.status == AssignmentStatus.COMPLETED:
        return assignment

    if assignment.status == AssignmentStatus.CANCELLED:
        raise ValidationError("Cannot complete a cancelled assignment.")

    as_of = as_of_date or timezone.localdate()
    if actual_end_date is None:
        actual_end_date = as_of

    if actual_end_date < assignment.start_date:
        raise ValidationError("actual_end_date cannot be before start_date.")
    if actual_end_date > as_of:
        raise ValidationError("actual_end_date cannot be in the future.")

    if home_time_days is not None:
        if home_time_days < 1 or home_time_days > 60:
            raise ValidationError("Home time days must be between 1 and 60.")
        assignment.home_time_days = home_time_days

    truck = assignment.truck
    next_planned = (
        RelayAssignment.objects.filter(
            truck=truck,
            status=AssignmentStatus.PLANNED,
            start_date__lte=actual_end_date,
        )
        .select_related("driver")
        .order_by("start_date")
        .first()
    )

    if next_planned is None and assignment.next_assignment_id:
        linked = assignment.next_assignment
        if (
            linked
            and linked.status == AssignmentStatus.PLANNED
            and linked.start_date <= actual_end_date
        ):
            next_planned = linked

    # Half-open handoff: truncate completed period if next starts earlier.
    if next_planned and next_planned.start_date < actual_end_date:
        if next_planned.start_date < assignment.start_date:
            raise ValidationError(
                "Next planned assignment starts before the current assignment."
            )
        actual_end_date = next_planned.start_date

    assignment.actual_end_date = actual_end_date
    assignment.status = AssignmentStatus.COMPLETED
    validate_assignment_overlap(assignment)
    assignment.save()

    _close_otr_status_period(assignment, actual_end_date)
    home_period = _create_home_time_period(assignment, actual_end_date)

    driver = assignment.driver
    if _home_time_period_is_open(home_period, as_of):
        driver.status = DriverStatus.HOME_TIME
    else:
        # Home window already ended (backdated completion) — available again.
        driver.status = DriverStatus.ACTIVE
    driver.save(update_fields=["status", "updated_at"])

    if next_planned:
        activated = activate_assignment(next_planned)
        # Catch-up: planned that starts and already ends on/before as_of.
        if as_of >= activated.effective_end_date:
            return complete_assignment(
                activated,
                actual_end_date=activated.effective_end_date,
                as_of_date=as_of,
            )
    else:
        truck.current_driver = None
        truck.status = TruckStatus.YARD
        truck.save(update_fields=["current_driver", "status", "updated_at"])

    return assignment


def get_open_home_time_period(driver: Driver) -> DriverStatusPeriod | None:
    """Latest HOME_TIME period that is still open as of today (start <= today < end)."""
    today = timezone.localdate()
    periods = (
        DriverStatusPeriod.objects.filter(
            driver=driver,
            status=DriverPeriodStatus.HOME_TIME,
        )
        .select_related("assignment", "assignment__truck")
        .order_by("-start_date", "-id")
    )
    for period in periods:
        if _home_time_period_is_open(period, today):
            return period
    return None


@transaction.atomic
def update_driver_home_time_period(
    period: DriverStatusPeriod,
    end_date: date,
) -> DriverStatusPeriod:
    """
    Change exclusive end of an open HOME_TIME DriverStatusPeriod.

    Syncs assignment.home_time_days when linked. If end_date <= today, marks
    the driver Active (available for work).
    """
    if period.status != DriverPeriodStatus.HOME_TIME:
        raise ValidationError("Only home time periods can be updated this way.")
    if end_date <= period.start_date:
        raise ValidationError("Home time end date must be after start date.")

    days = (end_date - period.start_date).days
    if days < 1 or days > 60:
        raise ValidationError("Home time duration must be between 1 and 60 days.")

    period.end_date = end_date
    period.save(update_fields=["end_date", "updated_at"])

    assignment = period.assignment
    if assignment is not None:
        assignment.home_time_days = days
        assignment.save(update_fields=["home_time_days", "updated_at"])

    driver = period.driver
    today = timezone.localdate()
    if end_date <= today:
        if driver.status == DriverStatus.HOME_TIME:
            driver.status = DriverStatus.ACTIVE
            driver.save(update_fields=["status", "updated_at"])
    elif period.start_date <= today < end_date:
        if driver.status != DriverStatus.HOME_TIME:
            driver.status = DriverStatus.HOME_TIME
            driver.save(update_fields=["status", "updated_at"])
    elif period.start_date > today and driver.status == DriverStatus.HOME_TIME:
        driver.status = DriverStatus.ACTIVE
        driver.save(update_fields=["status", "updated_at"])

    return period


@transaction.atomic
def assign_next_driver(
    previous_assignment: RelayAssignment,
    next_driver: Driver,
    start_date: date | None = None,
    cycle_weeks: int = DEFAULT_CYCLE_WEEKS,
    home_time_days: int = DEFAULT_HOME_TIME_DAYS,
    created_by: User | None = None,
    activate: bool = False,
) -> RelayAssignment:
    if start_date is None:
        # Immediate truck handoff: next may start when previous ends ([start, end)).
        start_date = previous_assignment.effective_end_date

    status = AssignmentStatus.ACTIVE if activate else AssignmentStatus.PLANNED

    next_assignment = create_assignment(
        driver=next_driver,
        truck=previous_assignment.truck,
        start_date=start_date,
        cycle_weeks=cycle_weeks,
        home_time_days=home_time_days,
        created_by=created_by,
        status=status,
    )

    next_assignment.previous_assignment = previous_assignment
    next_assignment.save(update_fields=["previous_assignment", "updated_at"])

    previous_assignment.next_assignment = next_assignment
    previous_assignment.save(update_fields=["next_assignment", "updated_at"])

    return next_assignment
