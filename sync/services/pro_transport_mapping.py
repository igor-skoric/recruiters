"""
Shared Pro Transport mapping helpers (no planning mutations).

Driver.driver_id = Pro Transport drivers.id (stable PK; not renamed).
Truck.protransport_id = Pro Transport trucks.id (stable PK).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.db import connections

from drivers.models import DriverType, EmploymentStatus

PRO_TRANSPORT_ALIAS = "pro_transport"

# Master sync may update ONLY these Driver fields (never status / notes / planning).
DRIVER_MASTER_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "phone",
        "email",
        "employment_status",
        "driver_type",
        "hire_date",
        "division",
        "last_synced_at",
    }
)

# Master sync may update ONLY these Truck fields (never status / current_driver / notes / vin).
TRUCK_MASTER_FIELDS = frozenset(
    {
        "unit_number",
        "make",
        "model",
        "year",
        "source_is_active",
        "division",
        "last_synced_at",
        "protransport_id",
    }
)

COMPANY_MASTER_FIELDS = frozenset(
    {
        "name",
        "dba",
        "mc",
        "us_dot_no",
        "phone",
        "email",
        "website",
        "mailing_city",
        "mailing_state",
        "last_synced_at",
    }
)

COMPANY_DATA_SQL = """
    SELECT
        id,
        division_name,
        dba,
        mc,
        us_dot_no,
        phone_no,
        email_address,
        website,
        ma_city,
        ma_state
    FROM company_data
    ORDER BY id
"""

DRIVERS_SQL = """
    SELECT
        id,
        first_name,
        last_name,
        phone_cell,
        phone_home,
        email,
        hire_date,
        status,
        is_company_driver,
        division_id
    FROM drivers
    ORDER BY id
"""

TRUCKS_SQL = """
    SELECT
        id,
        unit_no,
        make,
        model,
        year_of_truck,
        is_active,
        status,
        driver1_id,
        division_id
    FROM trucks
    ORDER BY id
"""
# Note: no WHERE here — rows are filtered on create via truck_allowed_for_import
# (source_is_active / total-loss). Existing local trucks are still updated.


@dataclass
class SyncCounts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # One-time link when local truck had unit_number but null protransport_id.
    linked_by_unit: int = 0
    conflicts: int = 0


@dataclass
class ImportResult:
    """Aggregate master-sync result."""

    companies_created: int = 0
    companies_updated: int = 0
    companies_unchanged: int = 0
    companies_skipped: int = 0
    drivers_created: int = 0
    drivers_updated: int = 0
    drivers_skipped: int = 0
    trucks_created: int = 0
    trucks_updated: int = 0
    trucks_skipped: int = 0
    drivers_unchanged: int = 0
    trucks_unchanged: int = 0
    trucks_linked_by_unit: int = 0
    assignments_linked: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ensure_pro_transport_configured() -> None:
    if PRO_TRANSPORT_ALIAS not in settings.DATABASES:
        raise ImproperlyConfigured(
            "Pro Transport DB is not configured. Set PRO_TRANSPORT_DB_HOST "
            "(and NAME/USER/PASSWORD) in the environment / .env.production."
        )


def map_employment_status(pt_status: Any) -> str:
    normalized = str(pt_status or "").strip().upper()
    if normalized == "ACTIVE":
        return EmploymentStatus.ACTIVE
    if normalized == "TERMINATED":
        return EmploymentStatus.TERMINATED
    if normalized in {"", "UNKNOWN"}:
        return EmploymentStatus.UNKNOWN
    return EmploymentStatus.INACTIVE


def map_driver_type(is_company_driver: Any) -> str:
    if is_company_driver is False:
        return DriverType.OWNER_OPERATOR
    return DriverType.COMPANY_DRIVER


def map_source_is_active(is_active: Any, pt_status: Any) -> bool:
    status_text = str(pt_status or "").strip().lower()
    if status_text == "total loss":
        return False
    if is_active is False:
        return False
    if is_active is True:
        return True
    return True


def extract_year(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, date):
        return value.year
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        if 1900 <= year <= 2100:
            return year
    return None


def extract_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for candidate, fmt in (
        (text[:19], "%Y-%m-%d %H:%M:%S"),
        (text[:10], "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def clean_str(value: Any, max_length: int | None = None) -> str:
    # Do not use `value or ""` — PT ids can be numeric 0.
    if value is None:
        text = ""
    else:
        text = str(value).strip()
    if max_length is not None:
        return text[:max_length]
    return text


def clean_email(value: Any) -> str:
    email = clean_str(value, 254)
    if not email:
        return ""
    try:
        validate_email(email)
    except ValidationError:
        return ""
    return email


def map_company_master_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("id") is None:
        raise ValueError("missing company_data id")
    pt_id = clean_str(row.get("id"), 64)
    if pt_id == "":
        raise ValueError("missing company_data id")
    name = clean_str(row.get("division_name"), 200) or f"Division {pt_id}"
    return {
        "protransport_id": pt_id,
        "name": name,
        "dba": clean_str(row.get("dba"), 200),
        "mc": clean_str(row.get("mc"), 50),
        "us_dot_no": clean_str(row.get("us_dot_no"), 50),
        "phone": clean_str(row.get("phone_no"), 30),
        "email": clean_email(row.get("email_address")),
        "website": clean_str(row.get("website"), 200),
        "mailing_city": clean_str(row.get("ma_city"), 100),
        "mailing_state": clean_str(row.get("ma_state"), 50),
    }


def map_driver_master_row(row: Mapping[str, Any]) -> dict[str, Any]:
    driver_id = clean_str(row.get("id"), 64)
    if not driver_id:
        raise ValueError("missing driver id")
    phone = clean_str(row.get("phone_cell"), 30) or clean_str(row.get("phone_home"), 30)
    division_pt_id = None
    if row.get("division_id") is not None and clean_str(row.get("division_id"), 64) != "":
        division_pt_id = clean_str(row.get("division_id"), 64)
    return {
        "driver_id": driver_id,
        "first_name": clean_str(row.get("first_name"), 100) or "Unknown",
        "last_name": clean_str(row.get("last_name"), 100) or "Unknown",
        "phone": phone,
        "email": clean_email(row.get("email")),
        "hire_date": extract_date(row.get("hire_date")),
        "employment_status": map_employment_status(row.get("status")),
        "driver_type": map_driver_type(row.get("is_company_driver")),
        "division_pt_id": division_pt_id,
    }


def map_truck_master_row(row: Mapping[str, Any]) -> dict[str, Any]:
    pt_id = clean_str(row.get("id"), 64)
    if not pt_id:
        raise ValueError("missing truck id")
    unit_number = clean_str(row.get("unit_no"), 50)
    if not unit_number:
        raise ValueError(f"missing unit_no for truck id={pt_id}")
    division_pt_id = None
    if row.get("division_id") is not None and clean_str(row.get("division_id"), 64) != "":
        division_pt_id = clean_str(row.get("division_id"), 64)
    return {
        "protransport_id": pt_id,
        "unit_number": unit_number,
        "make": clean_str(row.get("make"), 100),
        "model": clean_str(row.get("model"), 100),
        "year": extract_year(row.get("year_of_truck")),
        "source_is_active": map_source_is_active(row.get("is_active"), row.get("status")),
        "division_pt_id": division_pt_id,
    }


def fetch_pt_rows(sql: str) -> list[dict[str, Any]]:
    ensure_pro_transport_configured()
    with connections[PRO_TRANSPORT_ALIAS].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, values)) for values in cursor.fetchall()]
