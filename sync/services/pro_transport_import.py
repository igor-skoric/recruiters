"""Compatibility shim — prefer master_sync / bootstrap modules."""

from sync.services.master_sync import (  # noqa: F401
    ImportResult,
    sync_drivers,
    sync_master,
    sync_snapshot,
    sync_trucks,
    upsert_drivers_from_rows,
    upsert_trucks_from_rows,
)
from sync.services.pro_transport_mapping import (  # noqa: F401
    ensure_pro_transport_configured,
    extract_date,
    extract_year,
    map_driver_master_row as map_driver_row,
    map_employment_status as map_driver_status,
    map_driver_type,
    map_source_is_active,
    map_truck_master_row as map_truck_row,
)


def sync_current_driver_assignments() -> int:
    """Master sync never links current_driver / assignments."""
    return 0
