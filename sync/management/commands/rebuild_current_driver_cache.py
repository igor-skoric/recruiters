"""Rebuild Truck.current_driver cache from ACTIVE RelayAssignment rows."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from relay.models import AssignmentStatus, RelayAssignment
from trucks.models import Truck, TruckStatus


@dataclass
class RebuildCacheResult:
    set_from_active: int = 0
    cleared: int = 0
    unchanged: int = 0
    conflicts: list[str] = field(default_factory=list)


def rebuild_current_driver_cache(*, dry_run: bool = False) -> RebuildCacheResult:
    result = RebuildCacheResult()

    multi = (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .values("truck_id")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
    )
    for row in multi:
        result.conflicts.append(
            f"truck_id={row['truck_id']} has {row['c']} ACTIVE assignments"
        )

    active_by_truck: dict[int, int] = {}
    for assignment in (
        RelayAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .order_by("truck_id", "start_date", "id")
        .only("truck_id", "driver_id")
    ):
        # If conflict, still pick the earliest for reporting but leave conflict list.
        if assignment.truck_id not in active_by_truck:
            active_by_truck[assignment.truck_id] = assignment.driver_id

    with transaction.atomic():
        trucks = list(Truck.objects.select_for_update().order_by("id"))
        for truck in trucks:
            expected_driver_id = active_by_truck.get(truck.id)
            if expected_driver_id is None:
                if truck.current_driver_id is None:
                    result.unchanged += 1
                    continue
                if not dry_run:
                    truck.current_driver = None
                    # Do not force status; yard/available left to relay lifecycle.
                    truck.save(update_fields=["current_driver", "updated_at"])
                result.cleared += 1
            else:
                if truck.current_driver_id == expected_driver_id:
                    result.unchanged += 1
                    continue
                if not dry_run:
                    truck.current_driver_id = expected_driver_id
                    if truck.status == TruckStatus.AVAILABLE:
                        truck.status = TruckStatus.OTR
                        truck.save(
                            update_fields=["current_driver", "status", "updated_at"]
                        )
                    else:
                        truck.save(update_fields=["current_driver", "updated_at"])
                result.set_from_active += 1
    return result


class Command(BaseCommand):
    help = (
        "Rebuild Truck.current_driver from ACTIVE RelayAssignment "
        "(cache only; assignment remains source of truth)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report changes without writing.",
        )

    def handle(self, *args, **options):
        result = rebuild_current_driver_cache(dry_run=options["dry_run"])
        self.stdout.write(
            f"set_from_active={result.set_from_active} "
            f"cleared={result.cleared} unchanged={result.unchanged} "
            f"conflicts={len(result.conflicts)}"
        )
        for message in result.conflicts:
            self.stdout.write(self.style.WARNING(f"  conflict: {message}"))
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run only — no cache writes."))
        else:
            self.stdout.write(self.style.SUCCESS("Cache rebuild finished."))
