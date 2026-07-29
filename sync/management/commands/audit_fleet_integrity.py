"""Report Fleet Planner data-integrity issues (read-only by default)."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.management.base import BaseCommand
from django.db.models import Count

from drivers.models import Driver, EmploymentStatus
from relay.models import AssignmentStatus, RelayAssignment
from relay.services.relay_service import assignment_period_bounds
from trucks.models import Truck

_INACTIVE_EMPLOYMENT = frozenset(
    {EmploymentStatus.TERMINATED, EmploymentStatus.INACTIVE}
)


@dataclass
class AuditReport:
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def audit_fleet_integrity() -> AuditReport:
    report = AuditReport()

    multi_truck = (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .values("truck_id")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
    )
    for row in multi_truck:
        report.issues.append(
            f"truck_id={row['truck_id']} has {row['c']} ACTIVE assignments"
        )

    multi_driver = (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .values("driver_id")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
    )
    for row in multi_driver:
        report.issues.append(
            f"driver_id(pk)={row['driver_id']} has {row['c']} ACTIVE assignments"
        )

    active_by_truck: dict[int, int] = {}
    for assignment in (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .order_by("truck_id", "start_date", "id")
        .only("truck_id", "driver_id")
    ):
        if assignment.truck_id not in active_by_truck:
            active_by_truck[assignment.truck_id] = assignment.driver_id

    for truck in Truck.objects.only("id", "unit_number", "current_driver_id"):
        expected = active_by_truck.get(truck.id)
        if expected is None:
            if truck.current_driver_id is not None:
                report.issues.append(
                    f"cache mismatch: truck {truck.unit_number} current_driver set "
                    f"but no ACTIVE assignment"
                )
        elif truck.current_driver_id != expected:
            report.issues.append(
                f"cache mismatch: truck {truck.unit_number} current_driver_id="
                f"{truck.current_driver_id} expected={expected}"
            )

    for assignment in (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .select_related("truck", "driver")
    ):
        truck = assignment.truck
        driver = assignment.driver
        if truck.source_is_active is False:
            report.issues.append(
                f"ACTIVE assignment on PT-inactive truck {truck.unit_number} "
                f"(protransport_id={truck.protransport_id})"
            )
        if driver.employment_status in _INACTIVE_EMPLOYMENT:
            report.issues.append(
                f"ACTIVE assignment for PT-inactive driver {driver.full_name} "
                f"(driver_id={driver.driver_id}, employment={driver.employment_status})"
            )

    for truck in Truck.objects.filter(protransport_id__isnull=True).only("unit_number"):
        report.issues.append(f"truck without protransport_id: {truck.unit_number}")

    for driver in Driver.objects.filter(driver_id__isnull=True).only(
        "first_name", "last_name", "id"
    ):
        report.issues.append(
            f"driver without driver_id: pk={driver.pk} {driver.first_name} {driver.last_name}"
        )

    for assignment in RelayAssignment.objects.filter(
        start_date_is_estimated=True
    ).select_related("truck", "driver"):
        report.issues.append(
            f"estimated start_date not confirmed: assignment={assignment.pk} "
            f"truck={assignment.truck.unit_number} driver={assignment.driver.full_name} "
            f"start={assignment.start_date}"
        )

    open_assignments = list(
        RelayAssignment.objects.exclude(status=AssignmentStatus.CANCELLED)
        .select_related("truck", "driver")
        .order_by("truck_id", "start_date", "id")
    )
    by_truck: dict[int, list[RelayAssignment]] = {}
    by_driver: dict[int, list[RelayAssignment]] = {}
    for assignment in open_assignments:
        by_truck.setdefault(assignment.truck_id, []).append(assignment)
        by_driver.setdefault(assignment.driver_id, []).append(assignment)

    def _overlap_pairs(groups: dict[int, list[RelayAssignment]], kind: str) -> None:
        for _key, items in groups.items():
            for i, left in enumerate(items):
                left_start, left_end = assignment_period_bounds(left)
                for right in items[i + 1 :]:
                    right_start, right_end = assignment_period_bounds(right)
                    if left_start < right_end and right_start < left_end:
                        report.issues.append(
                            f"overlap ({kind}): assignments {left.pk} and {right.pk} "
                            f"({left.truck.unit_number} / {left.driver.full_name})"
                        )

    _overlap_pairs(by_truck, "truck")
    _overlap_pairs(by_driver, "driver")
    return report


class Command(BaseCommand):
    help = "Read-only fleet integrity audit (assignments, cache, PT ids, estimated dates)."

    def handle(self, *args, **options):
        report = audit_fleet_integrity()
        if report.ok:
            self.stdout.write(self.style.SUCCESS("audit_fleet_integrity: OK (no issues)"))
            return
        self.stdout.write(
            self.style.WARNING(f"audit_fleet_integrity: {len(report.issues)} issue(s)")
        )
        for issue in report.issues:
            self.stdout.write(f"  - {issue}")
