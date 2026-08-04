"""
Periodic Pro Transport master-data sync.

Order: company_data → drivers → trucks.
Never mutates RelayAssignment, DriverStatusPeriod, or Truck.current_driver.
Driver.status is only forced when PT employment becomes terminated/inactive
(so the local roster matches PT active headcount); ACTIVE employment does not
overwrite local home_time / planning ops status.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from django.db import transaction
from django.utils import timezone

from companies.models import CompanyData
from drivers.models import (
    Driver,
    DriverStatus,
    DriverType,
    EmploymentStatus,
    INACTIVE_EMPLOYMENT_STATUSES,
)
from relay.models import AssignmentStatus, RelayAssignment
from sync.services.pro_transport_mapping import (
    COMPANY_DATA_SQL,
    COMPANY_MASTER_FIELDS,
    DRIVER_MASTER_FIELDS,
    DRIVERS_SQL,
    TRUCK_MASTER_FIELDS,
    TRUCKS_SQL,
    ImportResult,
    SyncCounts,
    ensure_pro_transport_configured,
    fetch_pt_rows,
    map_company_master_row,
    map_driver_master_row,
    map_truck_master_row,
)
from trucks.models import Truck

_INACTIVE_EMPLOYMENT = INACTIVE_EMPLOYMENT_STATUSES

# Driver create allowlist for master sync — widen these later if needed.
# Existing drivers (already linked by driver_id) are still updated so PT
# employment/type stay accurate even when they fall outside the allowlist.
IMPORT_EMPLOYMENT_STATUSES = frozenset({EmploymentStatus.ACTIVE})
IMPORT_DRIVER_TYPES = frozenset({DriverType.COMPANY_DRIVER})

# Truck create allowlist — only PT-active trucks (not is_active=false / total loss).
# Existing trucks are still updated so source_is_active stays accurate.
IMPORT_TRUCKS_REQUIRE_SOURCE_ACTIVE = True


def driver_allowed_for_import(
    *,
    employment_status: str,
    driver_type: str,
) -> bool:
    """Return True if a PT driver row may create a new local Driver."""
    return (
        employment_status in IMPORT_EMPLOYMENT_STATUSES
        and driver_type in IMPORT_DRIVER_TYPES
    )


def truck_allowed_for_import(*, source_is_active: bool | None) -> bool:
    """Return True if a PT truck row may create a new local Truck."""
    if not IMPORT_TRUCKS_REQUIRE_SOURCE_ACTIVE:
        return True
    return source_is_active is True


def ops_status_from_employment(employment_status: str) -> str | None:
    """
    Mirror PT employment into local ops status when employment leaves the roster.

    ACTIVE employment does not force ops Active (home_time / planning stay local).
    New creates use Active separately.
    """
    if employment_status == EmploymentStatus.TERMINATED:
        return DriverStatus.TERMINATED
    if employment_status == EmploymentStatus.INACTIVE:
        return DriverStatus.INACTIVE
    return None


def _apply_fields(instance: Any, data: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    changed: list[str] = []
    for key in allowed:
        if key == "last_synced_at" or key not in data:
            continue
        if getattr(instance, key) != data[key]:
            setattr(instance, key, data[key])
            changed.append(key)
    return changed


def _resolve_division(
    counts: SyncCounts,
    *,
    context: str,
    division_pt_id: str | None,
) -> CompanyData | None:
    if not division_pt_id:
        return None
    company = CompanyData.objects.filter(protransport_id=division_pt_id).first()
    if company is None:
        counts.warnings.append(
            f"{context}: missing local CompanyData for division_id={division_pt_id}"
        )
    return company


def _warn_inactive_driver_with_active(
    counts: SyncCounts,
    driver: Driver,
    employment_status: str,
) -> None:
    if employment_status not in _INACTIVE_EMPLOYMENT:
        return
    if RelayAssignment.objects.filter(
        driver=driver, status=AssignmentStatus.ACTIVE
    ).exists():
        counts.warnings.append(
            f"driver_id={driver.driver_id}: PT employment={employment_status} "
            f"but local ACTIVE RelayAssignment exists (planning left untouched)"
        )


def _warn_inactive_truck_with_active(
    counts: SyncCounts,
    truck: Truck,
    source_is_active: bool | None,
) -> None:
    if source_is_active is not False:
        return
    if RelayAssignment.objects.filter(
        truck=truck, status=AssignmentStatus.ACTIVE
    ).exists():
        counts.warnings.append(
            f"protransport_id={truck.protransport_id} unit={truck.unit_number}: "
            f"PT source_is_active=false but local ACTIVE RelayAssignment exists "
            f"(planning left untouched)"
        )


def upsert_companies_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> SyncCounts:
    counts = SyncCounts()
    now = timezone.now()
    for index, row in enumerate(rows, start=1):
        try:
            data = map_company_master_row(row)
            existing = CompanyData.objects.filter(
                protransport_id=data["protransport_id"]
            ).first()
            if existing:
                would_change = [
                    key
                    for key in COMPANY_MASTER_FIELDS
                    if key != "last_synced_at"
                    and key in data
                    and getattr(existing, key) != data[key]
                ]
                if dry_run:
                    if would_change:
                        counts.updated += 1
                    else:
                        counts.unchanged += 1
                    continue
                with transaction.atomic():
                    changed = _apply_fields(existing, data, COMPANY_MASTER_FIELDS)
                    existing.last_synced_at = now
                    if changed:
                        existing.save(
                            update_fields=[*changed, "last_synced_at", "updated_at"]
                        )
                        counts.updated += 1
                    else:
                        existing.save(update_fields=["last_synced_at", "updated_at"])
                        counts.unchanged += 1
            else:
                if dry_run:
                    counts.created += 1
                    continue
                with transaction.atomic():
                    CompanyData.objects.create(
                        protransport_id=data["protransport_id"],
                        name=data["name"],
                        dba=data["dba"],
                        mc=data["mc"],
                        us_dot_no=data["us_dot_no"],
                        phone=data["phone"],
                        email=data["email"],
                        website=data["website"],
                        mailing_city=data["mailing_city"],
                        mailing_state=data["mailing_state"],
                        last_synced_at=now,
                    )
                    counts.created += 1
        except Exception as exc:  # noqa: BLE001
            counts.skipped += 1
            counts.conflicts += 1
            counts.errors.append(f"company row {index}: {exc}")
    return counts


def upsert_drivers_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> SyncCounts:
    counts = SyncCounts()
    now = timezone.now()
    for index, row in enumerate(rows, start=1):
        try:
            data = map_driver_master_row(row)
            division_pt_id = data.pop("division_pt_id", None)
            data["division"] = _resolve_division(
                counts,
                context=f"driver_id={data['driver_id']}",
                division_pt_id=division_pt_id,
            )
            existing = Driver.objects.filter(driver_id=data["driver_id"]).first()
            allowed = driver_allowed_for_import(
                employment_status=data["employment_status"],
                driver_type=data["driver_type"],
            )
            if existing is None and not allowed:
                counts.skipped += 1
                continue
            forced_ops = ops_status_from_employment(data["employment_status"])
            if existing:
                would_change = [
                    key
                    for key in DRIVER_MASTER_FIELDS
                    if key != "last_synced_at"
                    and key in data
                    and getattr(existing, key) != data[key]
                ]
                if forced_ops and existing.status != forced_ops:
                    would_change.append("status")
                _warn_inactive_driver_with_active(
                    counts, existing, data["employment_status"]
                )
                if dry_run:
                    if would_change:
                        counts.updated += 1
                    else:
                        counts.unchanged += 1
                    continue
                with transaction.atomic():
                    changed = _apply_fields(existing, data, DRIVER_MASTER_FIELDS)
                    if forced_ops and existing.status != forced_ops:
                        existing.status = forced_ops
                        changed = [*changed, "status"]
                    existing.last_synced_at = now
                    if changed:
                        existing.save(
                            update_fields=[*changed, "last_synced_at", "updated_at"]
                        )
                        counts.updated += 1
                    else:
                        existing.save(update_fields=["last_synced_at", "updated_at"])
                        counts.unchanged += 1
            else:
                if dry_run:
                    counts.created += 1
                    continue
                with transaction.atomic():
                    Driver.objects.create(
                        driver_id=data["driver_id"],
                        first_name=data["first_name"],
                        last_name=data["last_name"],
                        phone=data["phone"],
                        email=data["email"],
                        hire_date=data["hire_date"],
                        employment_status=data["employment_status"],
                        driver_type=data["driver_type"],
                        division=data["division"],
                        # New PT-active company drivers enter the ops roster as Active.
                        status=DriverStatus.ACTIVE,
                        last_synced_at=now,
                    )
                    counts.created += 1
        except Exception as exc:  # noqa: BLE001
            counts.skipped += 1
            counts.conflicts += 1
            counts.errors.append(f"driver row {index}: {exc}")
    return counts


def _find_truck_for_upsert(data: dict[str, Any]) -> tuple[Truck | None, bool]:
    existing = Truck.objects.filter(protransport_id=data["protransport_id"]).first()
    if existing:
        return existing, False

    unit = data["unit_number"]
    candidates = list(
        Truck.objects.filter(unit_number__iexact=unit, protransport_id__isnull=True)
    )
    if len(candidates) == 1:
        return candidates[0], True
    if len(candidates) > 1:
        raise ValueError(
            f"ambiguous unit_number={unit!r} for protransport_id="
            f"{data['protransport_id']}: {len(candidates)} local trucks"
        )
    conflict = (
        Truck.objects.filter(unit_number__iexact=unit)
        .exclude(protransport_id=data["protransport_id"])
        .first()
    )
    if conflict and conflict.protransport_id:
        raise ValueError(
            f"unit_number={unit!r} already linked to protransport_id="
            f"{conflict.protransport_id}; cannot claim {data['protransport_id']}"
        )
    return None, False


def upsert_trucks_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> SyncCounts:
    counts = SyncCounts()
    now = timezone.now()
    for index, row in enumerate(rows, start=1):
        try:
            data = map_truck_master_row(row)
            division_pt_id = data.pop("division_pt_id", None)
            data["division"] = _resolve_division(
                counts,
                context=f"truck protransport_id={data['protransport_id']}",
                division_pt_id=division_pt_id,
            )
            existing, linked = _find_truck_for_upsert(data)
            if existing is None and not truck_allowed_for_import(
                source_is_active=data["source_is_active"]
            ):
                counts.skipped += 1
                continue
            if existing:
                would_change = [
                    key
                    for key in TRUCK_MASTER_FIELDS
                    if key != "last_synced_at"
                    and key in data
                    and (
                        getattr(existing, key) != data[key]
                        or (linked and key == "protransport_id")
                    )
                ]
                if linked:
                    counts.linked_by_unit += 1
                _warn_inactive_truck_with_active(
                    counts, existing, data["source_is_active"]
                )
                if dry_run:
                    if would_change or linked:
                        counts.updated += 1
                    else:
                        counts.unchanged += 1
                    continue
                with transaction.atomic():
                    update_fields: set[str] = set()
                    if linked:
                        existing.protransport_id = data["protransport_id"]
                        update_fields.add("protransport_id")
                    changed = _apply_fields(existing, data, TRUCK_MASTER_FIELDS)
                    update_fields.update(changed)
                    existing.last_synced_at = now
                    update_fields.update({"last_synced_at", "updated_at"})
                    existing.save(update_fields=list(update_fields))
                    if changed or linked:
                        counts.updated += 1
                    else:
                        counts.unchanged += 1
            else:
                if dry_run:
                    counts.created += 1
                    continue
                with transaction.atomic():
                    Truck.objects.create(
                        protransport_id=data["protransport_id"],
                        unit_number=data["unit_number"],
                        make=data["make"],
                        model=data["model"],
                        year=data["year"],
                        source_is_active=data["source_is_active"],
                        division=data["division"],
                        last_synced_at=now,
                    )
                    counts.created += 1
        except Exception as exc:  # noqa: BLE001
            counts.skipped += 1
            counts.conflicts += 1
            counts.errors.append(f"truck row {index}: {exc}")
    return counts


def sync_companies(*, dry_run: bool = False) -> SyncCounts:
    return upsert_companies_from_rows(fetch_pt_rows(COMPANY_DATA_SQL), dry_run=dry_run)


def sync_drivers(*, dry_run: bool = False) -> SyncCounts:
    return upsert_drivers_from_rows(fetch_pt_rows(DRIVERS_SQL), dry_run=dry_run)


def sync_trucks(*, dry_run: bool = False) -> SyncCounts:
    return upsert_trucks_from_rows(fetch_pt_rows(TRUCKS_SQL), dry_run=dry_run)


def sync_master(*, dry_run: bool = False) -> ImportResult:
    """Periodic master sync: companies, then drivers, then trucks."""
    ensure_pro_transport_configured()
    companies = sync_companies(dry_run=dry_run)
    drivers = sync_drivers(dry_run=dry_run)
    trucks = sync_trucks(dry_run=dry_run)
    return ImportResult(
        companies_created=companies.created,
        companies_updated=companies.updated,
        companies_unchanged=companies.unchanged,
        companies_skipped=companies.skipped,
        drivers_created=drivers.created,
        drivers_updated=drivers.updated,
        drivers_unchanged=drivers.unchanged,
        drivers_skipped=drivers.skipped,
        trucks_created=trucks.created,
        trucks_updated=trucks.updated,
        trucks_unchanged=trucks.unchanged,
        trucks_skipped=trucks.skipped,
        trucks_linked_by_unit=trucks.linked_by_unit,
        dry_run=dry_run,
        errors=[*companies.errors, *drivers.errors, *trucks.errors],
        warnings=[*companies.warnings, *drivers.warnings, *trucks.warnings],
    )


def sync_snapshot(*, dry_run: bool = False) -> ImportResult:
    return sync_master(dry_run=dry_run)
