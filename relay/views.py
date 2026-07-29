from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from accounts.permissions import (
    ReadAccessRequiredMixin,
    create_access_required,
    delete_access_required,
    edit_access_required,
    full_access_required,
    user_can_create,
    user_can_delete,
    user_can_edit,
    user_has_full_access,
)
from drivers.models import Driver, DriverStatus, DriverType, EmploymentStatus
from relay.forms import (
    DriverCreateForm,
    PlanNextAssignmentForm,
    SpreadsheetImportForm,
    StartAssignmentForm,
    TruckCreateForm,
    UpdateDriverHomeTimePeriodForm,
    UpdateHomeTimeForm,
)
from relay.models import AssignmentStatus, RelayAssignment
from relay.presentation.truck_gantt import build_truck_gantt
from relay.presentation.driver_gantt import build_driver_gantt
from relay.services import records as record_service
from relay.services import relay_service
from relay.services import spreadsheet_import
from trucks.models import Truck, TruckStatus


PAGE_SIZE_OPTIONS = (20, 50, 100)
DEFAULT_PAGE_SIZE = 20


def _get_week_count(request) -> int:
    try:
        count = int(request.GET.get("weeks", relay_service.BOARD_WEEK_COUNT))
    except (TypeError, ValueError):
        count = relay_service.BOARD_WEEK_COUNT
    if count not in relay_service.ALLOWED_WEEK_COUNTS:
        count = relay_service.BOARD_WEEK_COUNT
    return count


def _get_board_start(request) -> date:
    today = timezone.localdate()
    raw_start = request.GET.get("start")
    if raw_start:
        parsed = parse_date(raw_start)
        if parsed:
            return parsed - timedelta(days=parsed.weekday())

    year_raw = request.GET.get("year")
    if year_raw:
        try:
            year = int(year_raw)
            return date.fromisocalendar(year, 1, 1)
        except (TypeError, ValueError):
            pass

    return today - timedelta(days=today.weekday())


def _zip_week_columns(week_headers, week_circles=None) -> list[dict]:
    if week_circles is None:
        return [{"header": header, "circle": None} for header in week_headers]
    return [
        {"header": header, "circle": circle}
        for header, circle in zip(week_headers, week_circles, strict=True)
    ]


def _period_nav(
    start: date,
    week_count: int,
    *,
    day: date | None = None,
    status: str = "",
    per_page: int | None = None,
) -> dict:
    today_monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    return {
        "prev_start": (start - timedelta(weeks=week_count)).isoformat(),
        "next_start": (start + timedelta(weeks=week_count)).isoformat(),
        "today_start": today_monday.isoformat(),
        "start": start.isoformat(),
        "week_options": sorted(relay_service.ALLOWED_WEEK_COUNTS),
        "year": start.isocalendar()[0],
        "day": day.isoformat() if day else "",
        "status": status or "",
        "per_page": per_page or DEFAULT_PAGE_SIZE,
    }


def _get_day_filter(request) -> date | None:
    raw = (request.GET.get("day") or "").strip()
    if not raw:
        return None
    return parse_date(raw)


def _get_status_filter(request) -> str:
    raw = (request.GET.get("status") or "").strip()
    allowed = {value for value, _label in relay_service.DAY_STATUS_FILTERS if value}
    if raw in allowed:
        return raw
    return ""


def _flatten_validation(exc: ValidationError) -> list[str]:
    if hasattr(exc, "message_dict"):
        messages_out = []
        for key, vals in exc.message_dict.items():
            for val in vals:
                messages_out.append(f"{key}: {val}" if key != "__all__" else str(val))
        return messages_out
    if hasattr(exc, "messages"):
        return [str(m) for m in exc.messages]
    return [str(exc)]


def _build_fleet_context(request, board_rows, week_count, start_date):
    week_headers = relay_service.get_board_week_headers(week_count, start_date=start_date)
    filter_day = _get_day_filter(request)
    filter_status = _get_status_filter(request)
    # Status filter only makes sense with a day; default day to today when status set.
    if filter_status and not filter_day:
        filter_day = timezone.localdate()

    filtered_rows = []
    for row in board_rows:
        day_info = None
        if filter_day:
            day_info = relay_service.get_truck_day_status(
                row,
                filter_day,
                assignments=row.assignments,
            )
            if filter_status and day_info["status"] != filter_status:
                continue
        filtered_rows.append((row, day_info))

    pagination = _paginate_queryset(request, filtered_rows)
    fleet_items = []
    for row, day_info in pagination["page_obj"].object_list:
        circles = relay_service.get_truck_week_circles(
            row,
            week_headers,
            assignments=row.assignments,
        )
        fleet_items.append(
            {
                "row": row,
                "week_circles": circles,
                "week_columns": _zip_week_columns(week_headers, circles),
                "day_status": day_info,
            }
        )

    per_page = pagination["per_page"]
    filters = {
        "start": start_date.isoformat(),
        "weeks": week_count,
        "day": filter_day.isoformat() if filter_day else "",
        "status": filter_status,
        "per_page": per_page,
    }

    return {
        "week_headers": week_headers,
        "week_columns_header": _zip_week_columns(week_headers),
        "week_count": week_count,
        "fleet_summary": relay_service.get_fleet_summary(board_rows),
        "fleet_items": fleet_items,
        "fleet_total_count": pagination["result_count"],
        "needs_review_count": sum(1 for row in board_rows if row.needs_review),
        "can_edit": user_has_full_access(request.user),
        "driver_form": DriverCreateForm(prefix="driver"),
        "truck_form": TruckCreateForm(prefix="new_truck"),
        "open_modal": None,
        "period_nav": _period_nav(
            start_date,
            week_count,
            day=filter_day,
            status=filter_status,
            per_page=per_page,
        ),
        "board_start": start_date,
        "filter_day": filter_day,
        "filter_status": filter_status,
        "filter_status_label": dict(relay_service.DAY_STATUS_FILTERS).get(filter_status, ""),
        "day_status_options": relay_service.DAY_STATUS_FILTERS,
        "filters": filters,
        **pagination,
    }


class FleetBoardView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/fleet_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board_rows = relay_service.get_relay_board()
        week_count = _get_week_count(self.request)
        start_date = _get_board_start(self.request)

        context.update({"page_title": "Fleet Planner"})
        context.update(_build_fleet_context(self.request, board_rows, week_count, start_date))
        if "driver_form" in kwargs:
            context["driver_form"] = kwargs["driver_form"]
        if "truck_form" in kwargs:
            context["truck_form"] = kwargs["truck_form"]
        if "open_modal" in kwargs:
            context["open_modal"] = kwargs["open_modal"]
        return context


def _render_fleet_board(request, *, driver_form=None, truck_form=None, open_modal=None):
    view = FleetBoardView()
    view.request = request
    view.kwargs = {}
    context = view.get_context_data(
        driver_form=driver_form or DriverCreateForm(prefix="driver"),
        truck_form=truck_form or TruckCreateForm(prefix="new_truck"),
        open_modal=open_modal,
    )
    return render(request, view.template_name, context)


class TruckDetailView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/truck_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        truck = get_object_or_404(
            Truck.objects.select_related("current_driver", "division"),
            pk=self.kwargs["pk"],
        )
        row = relay_service.get_truck_board_row(truck)
        week_count = _get_week_count(self.request)
        start_date = _get_board_start(self.request)
        week_headers = relay_service.get_board_week_headers(week_count, start_date=start_date)
        week_circles = relay_service.get_truck_week_circles(
            row,
            week_headers,
            assignments=row.assignments,
        )
        can_edit = user_has_full_access(self.request.user)
        current = relay_service.get_current_assignment_for_truck(truck)
        planned = relay_service.get_next_assignment_for_truck(truck)
        assignment_history = relay_service.get_truck_history(truck)
        # Presentation-only layout for the Gantt UI (no business logic).
        gantt = build_truck_gantt(assignment_history, week_headers)

        context.update(
            {
                "page_title": f"Truck {truck.unit_number}",
                "truck": truck,
                "row": row,
                "week_headers": week_headers,
                "week_count": week_count,
                "week_circles": week_circles,
                "week_columns": _zip_week_columns(week_headers, week_circles),
                "can_edit": can_edit,
                "assignment_history": assignment_history,
                "current_assignment": current,
                "next_assignment": planned,
                "period_nav": _period_nav(start_date, week_count),
                "gantt": gantt,
                "show_start_assignment": can_edit and current is None,
                "show_plan_next": can_edit and current is not None,
                "show_complete": can_edit and current is not None,
                "show_planned_actions": can_edit and planned is not None,
                "start_form": (
                    StartAssignmentForm(prefix="start", truck=truck) if can_edit and current is None else None
                ),
                "plan_form": (
                    PlanNextAssignmentForm(
                        prefix="plan",
                        truck=truck,
                        current_assignment=current,
                        planned=planned,
                    )
                    if can_edit and (current is not None or planned is not None)
                    else None
                ),
                "home_time_form": (
                    UpdateHomeTimeForm(prefix="home", assignment=current)
                    if can_edit and current is not None
                    else None
                ),
                "edit_truck_form": (
                    TruckCreateForm(prefix="edit_truck", instance=truck) if can_edit else None
                ),
                "edit_truck_action": (
                    reverse("relay:truck_edit", kwargs={"pk": truck.pk}) if can_edit else None
                ),
                "current_cycle_duration": (
                    relay_service.format_cycle_duration(
                        current.start_date, current.expected_end_date
                    )
                    if current
                    else None
                ),
                "next_cycle_duration": (
                    relay_service.format_cycle_duration(
                        planned.start_date, planned.expected_end_date
                    )
                    if planned
                    else None
                ),
            }
        )
        return context


class DriverDetailView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/driver_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        driver = get_object_or_404(
            Driver.objects.select_related("division"),
            pk=self.kwargs["pk"],
        )
        week_count = _get_week_count(self.request)
        start_date = _get_board_start(self.request)
        week_headers = relay_service.get_board_week_headers(week_count, start_date=start_date)
        history = relay_service.get_driver_history(driver)
        week_circles = relay_service.get_driver_week_circles(driver, week_headers)
        current = relay_service.get_current_assignment_for_driver(driver)
        can_edit = user_can_edit(self.request.user)
        home_period = relay_service.get_open_home_time_period(driver)
        gantt = build_driver_gantt(
            history["assignments"],
            history["status_periods"],
            week_headers,
        )

        context.update(
            {
                "page_title": f"Driver {driver.full_name}",
                "driver": driver,
                "history": history,
                "assignment_history": history["assignments"],
                "status_periods": history["status_periods"],
                "current_assignment": current,
                "week_headers": week_headers,
                "week_count": week_count,
                "week_columns": _zip_week_columns(week_headers, week_circles),
                "period_nav": _period_nav(start_date, week_count),
                "gantt": gantt,
                "can_edit": can_edit,
                "home_time_form": (
                    UpdateHomeTimeForm(prefix="home", assignment=current)
                    if can_edit and current is not None
                    else None
                ),
                "current_cycle_duration": (
                    relay_service.format_cycle_duration(
                        current.start_date, current.expected_end_date
                    )
                    if current
                    else None
                ),
                "home_time_period": home_period,
                "home_period_days": (
                    (home_period.end_date - home_period.start_date).days
                    if home_period and home_period.end_date
                    else None
                ),
                "home_period_form": (
                    UpdateDriverHomeTimePeriodForm(prefix="htperiod", period=home_period)
                    if can_edit and home_period is not None
                    else None
                ),
            }
        )
        planned = [
            a
            for a in history["assignments"]
            if a.status == AssignmentStatus.PLANNED and a.start_date >= timezone.localdate()
        ]
        context["next_assignment"] = min(planned, key=lambda a: a.start_date) if planned else None
        return context


@login_required
@require_POST
@edit_access_required
def driver_assignment_update_home_time_view(request, pk):
    driver = get_object_or_404(Driver, pk=pk)
    current = relay_service.get_current_assignment_for_driver(driver)
    if current is None:
        messages.error(request, "No active assignment to update.")
        return redirect("relay:driver_detail", pk=pk)

    form = UpdateHomeTimeForm(request.POST, prefix="home", assignment=current)
    if form.is_valid():
        try:
            assignment = relay_service.update_assignment_home_time(
                current,
                form.cleaned_data["home_time_date"],
                start_date=form.cleaned_data["start_date"],
            )
            relay_service.update_assignment_home_time_days(
                assignment,
                form.cleaned_data["home_time_days"],
            )
            relay_service.apply_home_time_side_effects(assignment)
            messages.success(
                request,
                (
                    f"Assignment dates updated. Truck {assignment.truck.unit_number} "
                    f"is free from {assignment.expected_end_date.strftime('%b %d, %Y')}."
                    + (
                        " Status set to Planned (start is in the future)."
                        if assignment.status == AssignmentStatus.PLANNED
                        else ""
                    )
                ),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(_flatten_validation(exc)))
    else:
        messages.error(request, "Could not update assignment dates.")
    return redirect("relay:driver_detail", pk=pk)


@login_required
@require_POST
@full_access_required
def assignment_start_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    if relay_service.get_current_assignment_for_truck(truck):
        messages.error(request, "Truck already has an active assignment.")
        return redirect("relay:truck_detail", pk=pk)

    form = StartAssignmentForm(request.POST, prefix="start", truck=truck)
    if form.is_valid():
        try:
            relay_service.create_assignment(
                driver=form.cleaned_data["driver"],
                truck=truck,
                start_date=form.cleaned_data["start_date"],
                expected_end_date=form.cleaned_data["home_time_date"],
                home_time_days=form.cleaned_data.get(
                    "home_time_days", relay_service.DEFAULT_HOME_TIME_DAYS
                ),
                status=AssignmentStatus.ACTIVE,
                notes=form.cleaned_data.get("notes", ""),
                created_by=request.user,
            )
            messages.success(request, f"Assignment started for truck {truck.unit_number}.")
        except ValidationError as exc:
            messages.error(request, "; ".join(_flatten_validation(exc)))
    else:
        messages.error(request, "Could not start assignment. Check the form.")
    return redirect("relay:truck_detail", pk=pk)


@login_required
@require_POST
@full_access_required
def assignment_plan_next_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    current = relay_service.get_current_assignment_for_truck(truck)
    planned = relay_service.get_next_assignment_for_truck(truck)
    if current is None and planned is None:
        messages.error(request, "Start an active assignment before planning the next driver.")
        return redirect("relay:truck_detail", pk=pk)

    form = PlanNextAssignmentForm(
        request.POST,
        prefix="plan",
        truck=truck,
        current_assignment=current,
        planned=planned,
    )
    if form.is_valid():
        try:
            relay_service.plan_next_assignment(
                truck,
                form.cleaned_data["next_driver"],
                form.cleaned_data["start_date"],
                expected_end_date=form.cleaned_data["home_time_date"],
                home_time_days=form.cleaned_data.get(
                    "home_time_days", relay_service.DEFAULT_HOME_TIME_DAYS
                ),
                notes=form.cleaned_data.get("notes", ""),
                created_by=request.user,
                existing=planned,
            )
            messages.success(request, f"Next assignment planned for truck {truck.unit_number}.")
        except ValidationError as exc:
            messages.error(request, "; ".join(_flatten_validation(exc)))
    else:
        messages.error(request, "Could not plan next assignment. Check the form.")
    return redirect("relay:truck_detail", pk=pk)


@login_required
@require_POST
@full_access_required
def assignment_update_home_time_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    current = relay_service.get_current_assignment_for_truck(truck)
    if current is None:
        messages.error(request, "No active assignment to update.")
        return redirect("relay:truck_detail", pk=pk)

    form = UpdateHomeTimeForm(request.POST, prefix="home", assignment=current)
    if form.is_valid():
        try:
            assignment = relay_service.update_assignment_home_time(
                current,
                form.cleaned_data["home_time_date"],
                start_date=form.cleaned_data["start_date"],
            )
            relay_service.update_assignment_home_time_days(
                assignment,
                form.cleaned_data["home_time_days"],
            )
            relay_service.apply_home_time_side_effects(assignment)
            messages.success(
                request,
                "Assignment dates updated."
                + (
                    " Status set to Planned (start is in the future)."
                    if assignment.status == AssignmentStatus.PLANNED
                    else ""
                ),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(_flatten_validation(exc)))
    else:
        messages.error(request, "Could not update assignment dates.")
    return redirect("relay:truck_detail", pk=pk)


@login_required
@require_POST
@full_access_required
def assignment_complete_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    current = relay_service.get_current_assignment_for_truck(truck)
    if current is None:
        messages.error(request, "No active assignment to complete.")
        return redirect("relay:truck_detail", pk=pk)

    try:
        end_raw = request.POST.get("actual_end_date")
        actual_end = parse_date(end_raw) if end_raw else timezone.localdate()
        if actual_end is None:
            raise ValidationError("Invalid end date.")
        days_raw = request.POST.get("home_time_days") or str(relay_service.DEFAULT_HOME_TIME_DAYS)
        try:
            home_time_days = int(days_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Home time days must be a number.") from exc
        relay_service.complete_assignment(
            current,
            actual_end_date=actual_end,
            home_time_days=home_time_days,
        )
        messages.success(request, f"Assignment completed for truck {truck.unit_number}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(_flatten_validation(exc)))
    return redirect("relay:truck_detail", pk=pk)


@login_required
@require_POST
@edit_access_required
def driver_home_time_period_update_view(request, pk):
    driver = get_object_or_404(Driver, pk=pk)
    period = relay_service.get_open_home_time_period(driver)
    if period is None:
        messages.error(request, "No open home time period to update.")
        return redirect("relay:driver_detail", pk=pk)

    form = UpdateDriverHomeTimePeriodForm(request.POST, prefix="htperiod", period=period)
    if form.is_valid():
        try:
            updated = relay_service.update_driver_home_time_period(
                period,
                form.cleaned_data["end_date"],
            )
            days = (updated.end_date - updated.start_date).days
            messages.success(
                request,
                f"Home time updated to {days} day{'s' if days != 1 else ''}.",
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(_flatten_validation(exc)))
    else:
        messages.error(request, "Could not update home time period.")
    return redirect("relay:driver_detail", pk=pk)


@login_required
@require_POST
@full_access_required
def assignment_cancel_planned_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    planned = relay_service.get_next_assignment_for_truck(truck)
    if planned is None:
        messages.error(request, "No planned assignment to cancel.")
        return redirect("relay:truck_detail", pk=pk)

    try:
        relay_service.cancel_planned_assignment(planned)
        messages.success(request, "Planned assignment cancelled.")
    except ValidationError as exc:
        messages.error(request, "; ".join(_flatten_validation(exc)))
    return redirect("relay:truck_detail", pk=pk)


class DriverListView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/drivers_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_drivers_list_context(self.request))
        return context


class TruckListView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/trucks_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_trucks_list_context(self.request))
        return context


def _capability_context(request) -> dict:
    return {
        "can_create": user_can_create(request.user),
        "can_edit": user_can_edit(request.user),
        "can_delete": user_can_delete(request.user),
        # Legacy alias used by truck detail / assignment templates.
        "can_manage": user_can_edit(request.user),
    }


def _parse_page_size(request) -> int:
    try:
        per_page = int(request.GET.get("per_page", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if per_page not in PAGE_SIZE_OPTIONS:
        return DEFAULT_PAGE_SIZE
    return per_page


def _paginate_queryset(request, queryset):
    per_page = _parse_page_size(request)
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    params = request.GET.copy()
    params.pop("page", None)
    return {
        "page_obj": page_obj,
        "paginator": paginator,
        "per_page": per_page,
        "page_size_options": PAGE_SIZE_OPTIONS,
        "page_numbers": _pagination_page_numbers(page_obj),
        "querystring": params.urlencode(),
        "result_count": paginator.count,
    }


def _pagination_page_numbers(page_obj) -> list[int | None]:
    """Return page numbers with None placeholders for ellipses."""
    current = page_obj.number
    total = page_obj.paginator.num_pages
    if total <= 9:
        return list(range(1, total + 1))

    selected = {1, total, current}
    for num in range(current - 2, current + 3):
        if 1 <= num <= total:
            selected.add(num)

    ordered = sorted(selected)
    numbers: list[int | None] = []
    previous = None
    for num in ordered:
        if previous is not None and num - previous > 1:
            numbers.append(None)
        numbers.append(num)
        previous = num
    return numbers


def _drivers_list_context(request, **overrides) -> dict:
    filters = _driver_list_filters(request)
    caps = _capability_context(request)
    pagination = _paginate_queryset(request, filters["queryset"])
    context = {
        "page_title": "Drivers",
        "drivers": pagination["page_obj"],
        "filters": {**filters["values"], "per_page": pagination["per_page"]},
        "filter_active": filters["active"],
        "status_choices": DriverStatus.choices,
        "employment_choices": EmploymentStatus.choices,
        "type_choices": DriverType.choices,
        **pagination,
        **caps,
        "driver_form": DriverCreateForm(prefix="driver"),
        "truck_form": TruckCreateForm(prefix="new_truck"),
        "edit_driver_form": DriverCreateForm(prefix="edit_driver"),
        "edit_truck_form": TruckCreateForm(prefix="edit_truck"),
        "import_form": SpreadsheetImportForm(),
        "open_modal": None,
        "edit_driver_action": "",
        "edit_truck_action": "",
    }
    context.update(overrides)
    return context


def _trucks_list_context(request, **overrides) -> dict:
    filters = _truck_list_filters(request)
    caps = _capability_context(request)
    pagination = _paginate_queryset(request, filters["queryset"])
    context = {
        "page_title": "Trucks",
        "trucks": pagination["page_obj"],
        "filters": {**filters["values"], "per_page": pagination["per_page"]},
        "filter_active": filters["active"],
        "status_choices": TruckStatus.choices,
        **pagination,
        **caps,
        "truck_form": TruckCreateForm(prefix="new_truck"),
        "driver_form": DriverCreateForm(prefix="driver"),
        "edit_driver_form": DriverCreateForm(prefix="edit_driver"),
        "edit_truck_form": TruckCreateForm(prefix="edit_truck"),
        "import_form": SpreadsheetImportForm(),
        "open_modal": None,
        "edit_driver_action": "",
        "edit_truck_action": "",
    }
    context.update(overrides)
    return context


def _driver_list_filters(request) -> dict:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    employment = (request.GET.get("employment") or "").strip()
    driver_type = (request.GET.get("type") or "").strip()

    queryset = Driver.objects.select_related("division")
    if status == "all":
        pass
    elif status and status in DriverStatus.values:
        queryset = queryset.filter(status=status)
    else:
        queryset = queryset.exclude(status=DriverStatus.TERMINATED)
        status = ""

    if employment and employment in EmploymentStatus.values:
        queryset = queryset.filter(employment_status=employment)
    else:
        employment = ""

    if driver_type and driver_type in DriverType.values:
        queryset = queryset.filter(driver_type=driver_type)
    else:
        driver_type = ""

    if q:
        queryset = queryset.annotate(
            _full_name=Concat("first_name", Value(" "), "last_name"),
        ).filter(
            Q(_full_name__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(driver_id__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(division__name__icontains=q)
            | Q(division__dba__icontains=q)
        )

    values = {
        "q": q,
        "status": status,
        "employment": employment,
        "type": driver_type,
    }
    active = bool(q or status or employment or driver_type)
    return {
        "queryset": queryset.order_by("last_name", "first_name"),
        "values": values,
        "active": active,
    }


def _truck_list_filters(request) -> dict:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    assigned = (request.GET.get("assigned") or "").strip()

    queryset = Truck.objects.select_related("current_driver", "division")
    if status and status in TruckStatus.values:
        queryset = queryset.filter(status=status)
    else:
        status = ""

    if assigned == "yes":
        queryset = queryset.filter(current_driver__isnull=False)
    elif assigned == "no":
        queryset = queryset.filter(current_driver__isnull=True)
    else:
        assigned = ""

    if q:
        queryset = queryset.annotate(
            _driver_full_name=Concat(
                "current_driver__first_name",
                Value(" "),
                "current_driver__last_name",
            ),
        ).filter(
            Q(unit_number__icontains=q)
            | Q(vin__icontains=q)
            | Q(make__icontains=q)
            | Q(model__icontains=q)
            | Q(_driver_full_name__icontains=q)
            | Q(current_driver__first_name__icontains=q)
            | Q(current_driver__last_name__icontains=q)
            | Q(current_driver__driver_id__icontains=q)
            | Q(division__name__icontains=q)
            | Q(division__dba__icontains=q)
        ).distinct()

    values = {"q": q, "status": status, "assigned": assigned}
    active = bool(q or status or assigned)
    return {
        "queryset": queryset.order_by("unit_number"),
        "values": values,
        "active": active,
    }


def _flash_import_result(request, kind: str, result: spreadsheet_import.SpreadsheetImportResult) -> None:
    label = "drivers" if kind == "drivers" else "trucks"
    if result.created or result.updated or result.assignments_created:
        parts = [f"{result.created} created", f"{result.updated} updated"]
        if result.assignments_created:
            parts.append(f"{result.assignments_created} start assignment(s)")
        if result.skipped:
            parts.append(f"{result.skipped} skipped")
        messages.success(request, f"Imported {label}: " + ", ".join(parts) + ".")
    elif result.skipped:
        messages.error(request, f"No {label} imported. {result.skipped} row(s) failed.")
    else:
        messages.warning(request, f"No {label} imported.")

    for error in result.errors[:8]:
        messages.error(request, error)


@login_required
@require_POST
@create_access_required
def drivers_import_view(request):
    form = SpreadsheetImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.get("file", ["Could not import file."])[0])
        return redirect("relay:drivers")

    try:
        rows = spreadsheet_import.read_spreadsheet_rows(form.cleaned_data["file"])
        result = spreadsheet_import.import_drivers(rows)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("relay:drivers")

    _flash_import_result(request, "drivers", result)
    return redirect("relay:drivers")


@login_required
@require_POST
@create_access_required
def trucks_import_view(request):
    form = SpreadsheetImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.get("file", ["Could not import file."])[0])
        return redirect("relay:trucks")

    create_assignments = request.POST.get("create_assignments") == "1"
    try:
        rows = spreadsheet_import.read_spreadsheet_rows(form.cleaned_data["file"])
        result = spreadsheet_import.import_trucks(
            rows,
            create_initial_assignments=create_assignments,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("relay:trucks")

    _flash_import_result(request, "trucks", result)
    return redirect("relay:trucks")


@login_required
@require_GET
@create_access_required
def import_template_view(request, kind: str):
    if kind not in {"drivers", "trucks"}:
        return redirect("relay:board")
    content = spreadsheet_import.build_template_csv(kind)
    response = HttpResponse(content, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{kind}_import_template.csv"'
    return response


@login_required
@require_POST
@create_access_required
def driver_create_view(request):
    form = DriverCreateForm(request.POST, prefix="driver")
    if form.is_valid():
        driver = form.save()
        messages.success(request, f"Driver {driver.full_name} added.")
        return redirect("relay:drivers")

    messages.error(request, "Could not add driver. Check the form.")
    return render(
        request,
        "relay/drivers_list.html",
        _drivers_list_context(request, driver_form=form, open_modal="driver"),
    )


@login_required
@require_POST
@edit_access_required
def driver_edit_view(request, pk):
    driver = get_object_or_404(Driver, pk=pk)
    form = DriverCreateForm(request.POST, instance=driver, prefix="edit_driver")
    if form.is_valid():
        driver = form.save()
        messages.success(request, f"Driver {driver.full_name} updated.")
        return redirect("relay:drivers")

    messages.error(request, "Could not update driver. Check the form.")
    return render(
        request,
        "relay/drivers_list.html",
        _drivers_list_context(
            request,
            edit_driver_form=form,
            edit_driver_action=reverse("relay:driver_edit", kwargs={"pk": pk}),
            open_modal="driver-edit",
        ),
    )


@login_required
@require_POST
@create_access_required
def truck_create_view(request):
    form = TruckCreateForm(request.POST, prefix="new_truck")
    if form.is_valid():
        truck = form.save(updated_by=request.user)
        messages.success(request, f"Truck {truck.unit_number} added.")
        return redirect("relay:trucks")

    messages.error(request, "Could not add truck. Check the form.")
    return render(
        request,
        "relay/trucks_list.html",
        _trucks_list_context(request, truck_form=form, open_modal="truck"),
    )


@login_required
@require_POST
@edit_access_required
def truck_edit_view(request, pk):
    truck = get_object_or_404(Truck.objects.select_related("current_driver"), pk=pk)
    form = TruckCreateForm(request.POST, instance=truck, prefix="edit_truck")
    if form.is_valid():
        truck = form.save(updated_by=request.user)
        messages.success(request, f"Truck {truck.unit_number} updated.")
        return redirect("relay:trucks")

    messages.error(request, "Could not update truck. Check the form.")
    return render(
        request,
        "relay/trucks_list.html",
        _trucks_list_context(
            request,
            edit_truck_form=form,
            edit_truck_action=reverse("relay:truck_edit", kwargs={"pk": pk}),
            open_modal="truck-edit",
        ),
    )


@login_required
@require_POST
@delete_access_required
def driver_delete_view(request, pk):
    driver = get_object_or_404(Driver, pk=pk)
    name = driver.full_name
    try:
        record_service.delete_driver(driver)
    except ValidationError as exc:
        messages.error(request, "; ".join(_flatten_validation(exc)))
        return redirect("relay:drivers")
    messages.success(request, f"Driver {name} deleted.")
    return redirect("relay:drivers")


@login_required
@require_POST
@delete_access_required
def truck_delete_view(request, pk):
    truck = get_object_or_404(Truck, pk=pk)
    unit = truck.unit_number
    try:
        record_service.delete_truck(truck)
    except ValidationError as exc:
        messages.error(request, "; ".join(_flatten_validation(exc)))
        return redirect("relay:trucks")
    messages.success(request, f"Truck {unit} deleted.")
    return redirect("relay:trucks")
