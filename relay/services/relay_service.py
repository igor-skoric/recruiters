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
)

if TYPE_CHECKING:
    from accounts.models import User

DEFAULT_CYCLE_WEEKS = 4
DEFAULT_HOME_TIME_WEEKS = 1
BOARD_WEEK_COUNT = 12
ALLOWED_WEEK_COUNTS = {8, 12, 16, 52}


class WeekCircleStatus:
    OTR = "otr"
    HOME = "home"
    REVIEW = "review"
    INACTIVE = "inactive"
    AVAILABLE = "available"


class TimelineStatus:
    OTR = "otr"
    HOME_TIME = "home_time"
    YARD = "yard"
    OPEN = "open"


@dataclass
class WeekSegment:
    week_start: date
    week_end: date
    status: str
    label: str
    assignment_id: int | None = None
    driver_name: str | None = None
    truck_unit: str | None = None


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


def _week_monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _home_period_end(home_start: date, home_weeks: int = DEFAULT_HOME_TIME_WEEKS) -> date:
    return home_start + timedelta(weeks=home_weeks) - timedelta(days=1)


def _default_next_driver_start(expected_home_date: date | None) -> date | None:
    if not expected_home_date:
        return None
    return _home_period_end(expected_home_date) + timedelta(days=1)


def _home_time_end(assignment: RelayAssignment) -> date:
    """Exclusive end of home-time period after assignment ends."""
    end = assignment.actual_end_date or assignment.expected_end_date
    return end + timedelta(weeks=assignment.home_time_weeks)


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


def _create_home_time_period(assignment: RelayAssignment, actual_end_date: date) -> DriverStatusPeriod:
    home_end = actual_end_date + timedelta(weeks=assignment.home_time_weeks)
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


def _get_active_assignment(truck: Truck) -> RelayAssignment | None:
    """Prefer true ACTIVE occupancy as of today; else keep legacy planned fallback."""
    current = get_current_assignment_for_truck(truck)
    if current:
        return current
    return (
        RelayAssignment.objects.filter(
            truck=truck,
            status__in={AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED},
        )
        .select_related("driver", "next_assignment__driver")
        .order_by("-start_date")
        .first()
    )


def _resolve_board_data(truck: Truck) -> dict:
    """
    Merge occupancy for the board.

    Priority:
    1. ACTIVE / next PLANNED RelayAssignment (historical source of truth)
    2. RelayStatusOverride (fallback corrections only)
    3. Truck.current_driver / Truck.status (cache / sync)
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

    current_driver = truck.current_driver
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
def save_status_override(
    truck: Truck,
    updated_by: User | None = None,
    *,
    cycle_start_date: date | None = None,
    expected_home_date: date | None = None,
    next_driver: Driver | None = None,
    next_driver_start_date: date | None = None,
    notes: str = "",
    status_override: str | None = None,
    update_cycle_start: bool = False,
    update_expected_home: bool = False,
    update_next_driver: bool = False,
    update_next_driver_start: bool = False,
    update_notes: bool = False,
    update_status: bool = False,
) -> RelayStatusOverride:
    """
    Create or update fallback corrections for a truck.

    Does not rewrite historical RelayAssignment rows. When assignments exist,
    board resolution prefers them over these override fields.
    """
    override, _created = RelayStatusOverride.objects.get_or_create(
        truck=truck,
        defaults={"updated_by": updated_by},
    )

    if update_cycle_start:
        override.cycle_start_date = cycle_start_date
        if cycle_start_date and not update_expected_home:
            override.expected_home_date = calculate_expected_end_date(cycle_start_date)

    if update_expected_home:
        override.expected_home_date = expected_home_date

    if update_next_driver:
        override.next_driver = next_driver
        if not next_driver:
            override.next_driver_start_date = None

    if update_next_driver_start:
        override.next_driver_start_date = next_driver_start_date
        if (
            override.next_driver_id
            and not override.next_driver_start_date
            and override.expected_home_date
        ):
            override.next_driver_start_date = _default_next_driver_start(
                override.expected_home_date
            )

    if update_notes:
        override.notes = notes

    if update_status:
        override.status_override = status_override or ""

    override.updated_by = updated_by
    override.save()
    return override


@transaction.atomic
def plan_next_assignment(
    truck: Truck,
    driver: Driver,
    start_date: date,
    *,
    cycle_weeks: int = DEFAULT_CYCLE_WEEKS,
    notes: str = "",
    created_by: User | None = None,
    existing: RelayAssignment | None = None,
) -> RelayAssignment:
    """Create or update a PLANNED assignment. Does not write RelayStatusOverride."""
    expected_end = calculate_expected_end_date(start_date, cycle_weeks)
    if existing is not None:
        if existing.status != AssignmentStatus.PLANNED:
            raise ValidationError("Only planned assignments can be edited here.")
        if existing.truck_id != truck.pk:
            raise ValidationError("Assignment does not belong to this truck.")
        existing.driver = driver
        existing.start_date = start_date
        existing.expected_end_date = expected_end
        existing.cycle_weeks = cycle_weeks
        existing.notes = notes
        existing.actual_end_date = None
        validate_assignment_overlap(existing)
        existing.save()
        return existing

    return create_assignment(
        driver=driver,
        truck=truck,
        start_date=start_date,
        cycle_weeks=cycle_weeks,
        expected_end_date=expected_end,
        status=AssignmentStatus.PLANNED,
        notes=notes,
        created_by=created_by,
    )


@transaction.atomic
def cancel_planned_assignment(assignment: RelayAssignment) -> RelayAssignment:
    if assignment.status != AssignmentStatus.PLANNED:
        raise ValidationError("Only planned assignments can be cancelled.")
    assignment.status = AssignmentStatus.CANCELLED
    assignment.save()
    return assignment


@transaction.atomic
def save_relay_plan(
    truck: Truck,
    updated_by: User | None = None,
    *,
    next_driver: Driver | None = None,
    next_driver_start_date: date | None = None,
    notes: str = "",
    status_override: str | None = None,
    cycle_start_date: date | None = None,
    expected_home_date: date | None = None,
) -> dict:
    """
    Legacy helper retained for tests/compatibility.

    Manual UI no longer writes override for planning — prefer plan_next_assignment().
    """
    result: dict = {"assignment": None, "override": None}

    if next_driver and next_driver_start_date:
        existing = get_next_assignment_for_truck(truck)
        result["assignment"] = plan_next_assignment(
            truck,
            next_driver,
            next_driver_start_date,
            notes=notes,
            created_by=updated_by,
            existing=existing,
        )
    elif next_driver and not next_driver_start_date:
        raise ValidationError(
            {"next_driver_start_date": "Start date is required when planning a next driver."}
        )

    return result


@dataclass
class ProcessRelayResult:
    activated: int = 0
    completed: int = 0
    yarded: int = 0


@transaction.atomic
def process_relay_state(as_of_date: date | None = None) -> ProcessRelayResult:
    """
    Idempotent as-of-today processor for planned/active handoffs.

    Safe to run via management command or before board render.
    Does not create duplicate DriverStatusPeriod rows.
    """
    as_of = as_of_date or timezone.localdate()
    result = ProcessRelayResult()

    # Complete ACTIVE assignments whose exclusive end has been reached.
    active_assignments = list(
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .select_related("truck", "driver")
        .order_by("start_date", "id")
    )
    for assignment in active_assignments:
        if as_of >= assignment.effective_end_date:
            before_driver = assignment.truck.current_driver_id
            complete_assignment(assignment, actual_end_date=assignment.effective_end_date)
            result.completed += 1
            assignment.truck.refresh_from_db()
            if assignment.truck.current_driver_id is None:
                result.yarded += 1
            elif assignment.truck.current_driver_id != before_driver:
                result.activated += 1

    # Activate PLANNED whose start has arrived and no conflicting ACTIVE remains.
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
        except ValidationError:
            continue

    return result


# --- Assignment lifecycle (source of truth) ---


def _iter_weeks(start_date: date, end_date: date) -> list[tuple[date, date]]:
    weeks: list[tuple[date, date]] = []
    current = start_date - timedelta(days=start_date.weekday())
    while current <= end_date:
        week_end = current + timedelta(days=6)
        weeks.append((current, week_end))
        current += timedelta(days=7)
    return weeks


def _week_overlaps(week_start: date, week_end: date, period_start: date, period_end: date) -> bool:
    return week_start <= period_end and week_end >= period_start


@transaction.atomic
def activate_assignment(assignment: RelayAssignment) -> RelayAssignment:
    """
    Activate a planned (or already active) assignment.

    Refuses when another ACTIVE assignment exists on the same truck — complete
    that assignment first for a valid handoff.
    """
    assignment = (
        RelayAssignment.objects.select_related("truck", "driver")
        .get(pk=assignment.pk)
    )

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
    home_time_weeks: int = DEFAULT_HOME_TIME_WEEKS,
    created_by: User | None = None,
    status: str = AssignmentStatus.ACTIVE,
    notes: str = "",
    expected_end_date: date | None = None,
) -> RelayAssignment:
    if expected_end_date is None:
        expected_end_date = calculate_expected_end_date(start_date, cycle_weeks)

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
        home_time_weeks=home_time_weeks,
        status=create_status,
        notes=notes,
        created_by=created_by,
    )
    validate_assignment_overlap(assignment)
    assignment.save()

    if status == AssignmentStatus.ACTIVE:
        return activate_assignment(assignment)
    return assignment


@transaction.atomic
def complete_assignment(
    assignment: RelayAssignment,
    actual_end_date: date | None = None,
) -> RelayAssignment:
    assignment = (
        RelayAssignment.objects.select_related("truck", "driver", "next_assignment")
        .get(pk=assignment.pk)
    )

    if assignment.status == AssignmentStatus.COMPLETED:
        return assignment

    if assignment.status == AssignmentStatus.CANCELLED:
        raise ValidationError("Cannot complete a cancelled assignment.")

    if actual_end_date is None:
        actual_end_date = timezone.localdate()

    if actual_end_date < assignment.start_date:
        raise ValidationError("actual_end_date cannot be before start_date.")

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
    _create_home_time_period(assignment, actual_end_date)

    driver = assignment.driver
    driver.status = DriverStatus.HOME_TIME
    driver.save(update_fields=["status", "updated_at"])

    if next_planned:
        activate_assignment(next_planned)
    else:
        truck.current_driver = None
        truck.status = TruckStatus.YARD
        truck.save(update_fields=["current_driver", "status", "updated_at"])

    return assignment


@transaction.atomic
def assign_next_driver(
    previous_assignment: RelayAssignment,
    next_driver: Driver,
    start_date: date | None = None,
    cycle_weeks: int = DEFAULT_CYCLE_WEEKS,
    home_time_weeks: int = DEFAULT_HOME_TIME_WEEKS,
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
        home_time_weeks=home_time_weeks,
        created_by=created_by,
        status=status,
    )

    next_assignment.previous_assignment = previous_assignment
    next_assignment.save(update_fields=["previous_assignment", "updated_at"])

    previous_assignment.next_assignment = next_assignment
    previous_assignment.save(update_fields=["next_assignment", "updated_at"])

    return next_assignment


def get_driver_timeline(
    driver: Driver,
    start_date: date,
    end_date: date,
) -> list[WeekSegment]:
    assignments = RelayAssignment.objects.filter(
        driver=driver,
        start_date__lte=end_date,
    ).exclude(status=AssignmentStatus.CANCELLED)

    segments: list[WeekSegment] = []
    for week_start, week_end in _iter_weeks(start_date, end_date):
        status = TimelineStatus.OPEN
        label = "Open"
        assignment_id = None
        truck_unit = None

        for assignment in assignments:
            otr_end = assignment.expected_end_date - timedelta(days=1)
            if assignment.status == AssignmentStatus.COMPLETED and assignment.actual_end_date:
                otr_end = assignment.actual_end_date - timedelta(days=1)

            otr_start = assignment.start_date
            home_start = assignment.actual_end_date or assignment.expected_end_date
            home_end = _home_time_end(assignment) - timedelta(days=1)

            if _week_overlaps(week_start, week_end, otr_start, otr_end):
                status = TimelineStatus.OTR
                label = f"OTR — {assignment.truck.unit_number}"
                assignment_id = assignment.id
                truck_unit = assignment.truck.unit_number
                break

            if assignment.status == AssignmentStatus.COMPLETED and _week_overlaps(
                week_start, week_end, home_start, home_end
            ):
                status = TimelineStatus.HOME_TIME
                label = "Home Time"
                assignment_id = assignment.id
                break

        segments.append(
            WeekSegment(
                week_start=week_start,
                week_end=week_end,
                status=status,
                label=label,
                assignment_id=assignment_id,
                truck_unit=truck_unit,
            )
        )

    return segments


def get_truck_timeline(
    truck: Truck,
    start_date: date,
    end_date: date,
) -> list[WeekSegment]:
    data = _resolve_board_data(truck)
    assignments = RelayAssignment.objects.filter(
        truck=truck,
        start_date__lte=end_date,
    ).exclude(status=AssignmentStatus.CANCELLED).select_related("driver")

    segments: list[WeekSegment] = []
    for week_start, week_end in _iter_weeks(start_date, end_date):
        status = TimelineStatus.OPEN
        label = "Needs Driver"
        assignment_id = None
        driver_name = None

        if data["cycle_start_date"] and data["expected_home_date"]:
            otr_end = data["expected_home_date"] - timedelta(days=1)
            if _week_overlaps(week_start, week_end, data["cycle_start_date"], otr_end):
                status = TimelineStatus.OTR
                label = f"OTR — {data['current_driver'].full_name if data['current_driver'] else 'Unknown'}"
                driver_name = data["current_driver"].full_name if data["current_driver"] else None

        if status == TimelineStatus.OPEN:
            for assignment in assignments:
                otr_end = assignment.expected_end_date - timedelta(days=1)
                if _week_overlaps(week_start, week_end, assignment.start_date, otr_end):
                    status = TimelineStatus.OTR
                    label = f"OTR — {assignment.driver.full_name}"
                    assignment_id = assignment.id
                    driver_name = assignment.driver.full_name
                    break

        if status == TimelineStatus.OPEN:
            if data["truck_status"] == TruckStatus.YARD:
                status = TimelineStatus.YARD
                label = "Yard"
            elif not data["current_driver"]:
                status = TimelineStatus.OPEN
                label = "Needs Driver"

        segments.append(
            WeekSegment(
                week_start=week_start,
                week_end=week_end,
                status=status,
                label=label,
                assignment_id=assignment_id,
                driver_name=driver_name,
                truck_unit=truck.unit_number,
            )
        )

    return segments
