from django import template

from drivers.models import Driver, DriverStatus

register = template.Library()


@register.simple_tag
def kpi_drivers_home_time() -> int:
    """Display-only KPI: count drivers currently on home time."""
    return Driver.objects.filter(status=DriverStatus.HOME_TIME).count()


@register.simple_tag
def kpi_cycles_ending_soon(fleet_items, days: int = 7) -> int:
    """Display-only KPI: trucks whose cycle ends within N days (from board row)."""
    count = 0
    for item in fleet_items or []:
        row = item["row"] if isinstance(item, dict) else item
        if row.days_left is not None and 0 <= row.days_left <= days:
            count += 1
    return count


@register.inclusion_tag("relay/partials/_planning_badge.html")
def planning_badge(row):
    """Map existing review_status to Ready / Needs Planning / Conflict / Inactive."""
    status = row.review_status
    truck_status = row.truck_status

    if truck_status in {"maintenance", "inactive"} or status == "maintenance":
        return {"tone": "inactive", "label": "Inactive"}
    if status == "ok" or status == "in_yard":
        return {"tone": "ready", "label": "Ready"}
    if status in {
        "needs_relay_planning",
        "missing_cycle_start_date",
        "missing_current_driver",
    }:
        return {"tone": "needs", "label": "Needs Planning"}
    if row.needs_review:
        return {"tone": "needs", "label": "Needs Planning"}
    return {"tone": "inactive", "label": "Inactive"}


@register.filter
def assignment_badge_class(status: str) -> str:
    mapping = {
        "active": "badge-status-active",
        "planned": "badge-status-planned",
        "completed": "badge-status-completed",
        "cancelled": "badge-status-cancelled",
    }
    return mapping.get(status, "badge-status-cancelled")
