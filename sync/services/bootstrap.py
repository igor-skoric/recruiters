"""
One-time / explicit Pro Transport bootstrap of ACTIVE RelayAssignments.

Match keys ONLY:
  Truck.protransport_id ↔ PT trucks.id
  Driver.driver_id ↔ PT drivers.id

Never invents start dates silently — requires --default-start-date or setting.
Never resolves conflicts by guessing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.dateparse import parse_date

from drivers.models import Driver, EmploymentStatus, INACTIVE_EMPLOYMENT_STATUSES
from relay.models import AssignmentStatus, RelayAssignment
from relay.services import relay_service
from sync.services.master_sync import sync_master
from sync.services.pro_transport_mapping import (
    TRUCKS_SQL,
    clean_str,
    ensure_pro_transport_configured,
    fetch_pt_rows,
    map_source_is_active,
)
from trucks.models import Truck

ESTIMATED_START_NOTE = (
    "Bootstrap from Pro Transport: start_date estimated "
    "(default/cutover). Review and correct in UI if needed."
)

_INACTIVE_EMPLOYMENT = INACTIVE_EMPLOYMENT_STATUSES


@dataclass
class BootstrapResult:
    assignments_created: int = 0
    already_matched: int = 0
    skipped_existing_planning: int = 0
    truck_has_active_assignment: int = 0
    driver_has_active_assignment: int = 0
    missing_driver: int = 0
    missing_truck: int = 0
    ambiguous_relation: int = 0
    missing_start_date: int = 0
    no_pt_driver: int = 0
    duplicate_pt_truck_rows: int = 0
    driver_current_on_multiple_trucks: int = 0
    inactive_pt_driver_with_relation: int = 0
    inactive_pt_truck_with_relation: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    def as_lines(self) -> list[str]:
        return [
            f"dry_run={self.dry_run}",
            f"assignments_created={self.assignments_created}",
            f"already_matched={self.already_matched}",
            f"skipped_existing_planning={self.skipped_existing_planning}",
            f"truck_has_active_assignment={self.truck_has_active_assignment}",
            f"driver_has_active_assignment={self.driver_has_active_assignment}",
            f"missing_driver={self.missing_driver}",
            f"missing_truck={self.missing_truck}",
            f"ambiguous_relation={self.ambiguous_relation}",
            f"missing_start_date={self.missing_start_date}",
            f"no_pt_driver={self.no_pt_driver}",
            f"duplicate_pt_truck_rows={self.duplicate_pt_truck_rows}",
            f"driver_current_on_multiple_trucks={self.driver_current_on_multiple_trucks}",
            f"inactive_pt_driver_with_relation={self.inactive_pt_driver_with_relation}",
            f"inactive_pt_truck_with_relation={self.inactive_pt_truck_with_relation}",
            f"warnings={len(self.warnings)}",
            f"errors={len(self.errors)}",
        ]


def resolve_default_start_date(
    explicit: date | str | None = None,
) -> date | None:
    if isinstance(explicit, date):
        return explicit
    if isinstance(explicit, str) and explicit.strip():
        parsed = parse_date(explicit.strip())
        if parsed:
            return parsed
        raise ValueError(f"Invalid default start date: {explicit!r}")
    configured = getattr(settings, "PRO_TRANSPORT_BOOTSTRAP_DEFAULT_START_DATE", "") or ""
    if configured:
        parsed = parse_date(str(configured).strip())
        if parsed:
            return parsed
        raise ValueError(
            f"Invalid PRO_TRANSPORT_BOOTSTRAP_DEFAULT_START_DATE: {configured!r}"
        )
    return None


def _truck_has_open_planning(truck: Truck) -> bool:
    return RelayAssignment.objects.filter(
        truck=truck,
        status__in={AssignmentStatus.ACTIVE, AssignmentStatus.PLANNED},
    ).exists()


def _active_matches(truck: Truck, driver: Driver) -> bool:
    return RelayAssignment.objects.filter(
        truck=truck,
        driver=driver,
        status=AssignmentStatus.ACTIVE,
    ).exists()


def _preflight_pt_rows(
    rows: list[Mapping[str, Any]],
    result: BootstrapResult,
) -> tuple[set[str], set[str]]:
    """
    Detect duplicate PT truck rows and drivers assigned as current on multiple trucks.
    Returns (duplicate_truck_ids, multi_truck_driver_ids) to skip without guessing.
    """
    truck_seen: dict[str, int] = {}
    driver_trucks: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        pt_truck_id = clean_str(row.get("id"), 64)
        pt_driver_id = clean_str(row.get("driver1_id"), 64)
        if not pt_truck_id:
            continue
        truck_seen[pt_truck_id] = truck_seen.get(pt_truck_id, 0) + 1
        if pt_driver_id:
            driver_trucks[pt_driver_id].add(pt_truck_id)

    duplicate_trucks = {tid for tid, count in truck_seen.items() if count > 1}
    multi_drivers = {
        did for did, trucks in driver_trucks.items() if len(trucks) > 1
    }

    for tid in sorted(duplicate_trucks):
        result.duplicate_pt_truck_rows += 1
        result.warnings.append(
            f"duplicate PT truck rows for protransport_id={tid} — skipped (no guess)"
        )
    for did in sorted(multi_drivers):
        result.driver_current_on_multiple_trucks += 1
        trucks = ",".join(sorted(driver_trucks[did]))
        result.warnings.append(
            f"PT driver_id={did} is current on multiple trucks ({trucks}) — "
            f"skipped (no guess)"
        )
    return duplicate_trucks, multi_drivers


def bootstrap_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    default_start_date: date | None,
    dry_run: bool = False,
) -> BootstrapResult:
    result = BootstrapResult(dry_run=dry_run)
    row_list = list(rows)
    duplicate_trucks, multi_drivers = _preflight_pt_rows(row_list, result)

    for index, row in enumerate(row_list, start=1):
        try:
            pt_truck_id = clean_str(row.get("id"), 64)
            pt_driver_id = clean_str(row.get("driver1_id"), 64)
            if not pt_truck_id:
                result.errors.append(f"row {index}: missing truck id")
                continue

            if pt_truck_id in duplicate_trucks:
                result.ambiguous_relation += 1
                continue

            if not pt_driver_id:
                result.no_pt_driver += 1
                result.warnings.append(
                    f"row {index}: PT truck protransport_id={pt_truck_id} has no current driver"
                )
                continue

            if pt_driver_id in multi_drivers:
                result.ambiguous_relation += 1
                continue

            # Match ONLY by stable PT primary keys.
            truck = Truck.objects.filter(protransport_id=pt_truck_id).first()
            if not truck:
                result.missing_truck += 1
                result.errors.append(
                    f"row {index}: missing local truck for protransport_id={pt_truck_id}"
                )
                continue

            drivers = list(Driver.objects.filter(driver_id=pt_driver_id))
            if not drivers:
                result.missing_driver += 1
                result.errors.append(
                    f"row {index}: missing local driver for driver_id={pt_driver_id} "
                    f"(protransport_id={pt_truck_id})"
                )
                continue
            if len(drivers) > 1:
                result.ambiguous_relation += 1
                result.errors.append(
                    f"row {index}: ambiguous driver_id={pt_driver_id}"
                )
                continue
            driver = drivers[0]

            pt_truck_active = map_source_is_active(row.get("is_active"), row.get("status"))
            if not pt_truck_active:
                result.inactive_pt_truck_with_relation += 1
                result.warnings.append(
                    f"row {index}: inactive PT truck protransport_id={pt_truck_id} "
                    f"has current driver — skipped"
                )
                continue

            if driver.employment_status in _INACTIVE_EMPLOYMENT:
                result.inactive_pt_driver_with_relation += 1
                result.warnings.append(
                    f"row {index}: inactive/terminated PT driver driver_id={pt_driver_id} "
                    f"is current on truck protransport_id={pt_truck_id} — skipped"
                )
                continue

            if _active_matches(truck, driver):
                result.already_matched += 1
                continue

            if RelayAssignment.objects.filter(
                truck=truck, status=AssignmentStatus.ACTIVE
            ).exists():
                result.truck_has_active_assignment += 1
                result.skipped_existing_planning += 1
                result.warnings.append(
                    f"row {index}: local truck protransport_id={pt_truck_id} "
                    f"already has ACTIVE assignment — skipped"
                )
                continue

            if RelayAssignment.objects.filter(
                truck=truck, status=AssignmentStatus.PLANNED
            ).exists():
                result.skipped_existing_planning += 1
                result.warnings.append(
                    f"row {index}: local truck protransport_id={pt_truck_id} "
                    f"already has PLANNED assignment — skipped"
                )
                continue

            if RelayAssignment.objects.filter(
                driver=driver, status=AssignmentStatus.ACTIVE
            ).exists():
                result.driver_has_active_assignment += 1
                result.skipped_existing_planning += 1
                result.warnings.append(
                    f"row {index}: local driver driver_id={pt_driver_id} "
                    f"already has ACTIVE assignment — skipped"
                )
                continue

            if default_start_date is None:
                result.missing_start_date += 1
                result.errors.append(
                    f"row {index}: no start date — pass --default-start-date "
                    f"or set PRO_TRANSPORT_BOOTSTRAP_DEFAULT_START_DATE "
                    f"(protransport_id={pt_truck_id})"
                )
                continue

            if dry_run:
                result.assignments_created += 1
                continue

            with transaction.atomic():
                locked_truck = Truck.objects.select_for_update().get(pk=truck.pk)
                if _truck_has_open_planning(locked_truck):
                    result.skipped_existing_planning += 1
                    continue
                if RelayAssignment.objects.filter(
                    driver=driver, status=AssignmentStatus.ACTIVE
                ).exists():
                    result.driver_has_active_assignment += 1
                    result.skipped_existing_planning += 1
                    continue
                relay_service.create_assignment(
                    driver=driver,
                    truck=locked_truck,
                    start_date=default_start_date,
                    status=AssignmentStatus.ACTIVE,
                    notes=ESTIMATED_START_NOTE,
                    start_date_is_estimated=True,
                )
                result.assignments_created += 1
        except ValidationError as exc:
            result.errors.append(f"row {index}: {exc}")
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"row {index}: {exc}")
    return result


def bootstrap_assignments(
    *,
    confirm: bool,
    dry_run: bool = False,
    run_master_sync: bool = True,
    default_start_date: date | str | None = None,
) -> BootstrapResult:
    if not confirm and not dry_run:
        raise ValidationError(
            "Bootstrap requires --confirm (or use --dry-run). "
            "This creates ACTIVE RelayAssignments from Pro Transport."
        )

    ensure_pro_transport_configured()
    start = resolve_default_start_date(default_start_date)

    if run_master_sync and not dry_run:
        sync_master()

    rows = fetch_pt_rows(TRUCKS_SQL)
    return bootstrap_rows(rows, default_start_date=start, dry_run=dry_run)
