from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from accounts.permissions import (
    ReadAccessRequiredMixin,
    full_access_required,
    user_has_full_access,
)
from drivers.models import Driver, DriverStatus
from relay.forms import (
    DriverCreateForm,
    PlanNextAssignmentForm,
    StartAssignmentForm,
    TruckCreateForm,
)
from relay.models import AssignmentStatus, RelayAssignment
from relay.services import relay_service
from trucks.models import Truck


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


def _period_nav(start: date, week_count: int) -> dict:
    today_monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
    return {
        "prev_start": (start - timedelta(weeks=week_count)).isoformat(),
        "next_start": (start + timedelta(weeks=week_count)).isoformat(),
        "today_start": today_monday.isoformat(),
        "start": start.isoformat(),
        "week_options": sorted(relay_service.ALLOWED_WEEK_COUNTS),
        "year": start.isocalendar()[0],
    }


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
    fleet_items = []
    for row in board_rows:
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
            }
        )

    return {
        "week_headers": week_headers,
        "week_columns_header": _zip_week_columns(week_headers),
        "week_count": week_count,
        "fleet_summary": relay_service.get_fleet_summary(board_rows),
        "fleet_items": fleet_items,
        "needs_review_count": sum(1 for row in board_rows if row.needs_review),
        "can_edit": user_has_full_access(request.user),
        "driver_form": DriverCreateForm(prefix="driver"),
        "truck_form": TruckCreateForm(prefix="new_truck"),
        "open_modal": None,
        "period_nav": _period_nav(start_date, week_count),
        "board_start": start_date,
    }


class FleetBoardView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/fleet_board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        relay_service.process_relay_state()
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
        truck = get_object_or_404(Truck, pk=self.kwargs["pk"])
        relay_service.process_relay_state()
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
                "assignment_history": relay_service.get_truck_history(truck),
                "current_assignment": current,
                "next_assignment": planned,
                "period_nav": _period_nav(start_date, week_count),
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
                    if can_edit and current is not None
                    else None
                ),
            }
        )
        return context


class DriverDetailView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/driver_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        driver = get_object_or_404(Driver, pk=self.kwargs["pk"])
        relay_service.process_relay_state()
        week_count = _get_week_count(self.request)
        start_date = _get_board_start(self.request)
        week_headers = relay_service.get_board_week_headers(week_count, start_date=start_date)
        history = relay_service.get_driver_history(driver)
        week_circles = relay_service.get_driver_week_circles(driver, week_headers)
        current = relay_service.get_current_assignment_for_driver(driver)

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
                cycle_weeks=form.cleaned_data["cycle_weeks"],
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
    if current is None:
        messages.error(request, "Start an active assignment before planning the next driver.")
        return redirect("relay:truck_detail", pk=pk)

    planned = relay_service.get_next_assignment_for_truck(truck)
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
                cycle_weeks=form.cleaned_data["cycle_weeks"],
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
        relay_service.complete_assignment(current, actual_end_date=actual_end)
        messages.success(request, f"Assignment completed for truck {truck.unit_number}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(_flatten_validation(exc)))
    return redirect("relay:truck_detail", pk=pk)


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
        context.update(
            {
                "page_title": "Drivers",
                "drivers": Driver.objects.exclude(status=DriverStatus.TERMINATED).order_by(
                    "last_name", "first_name"
                ),
                "can_edit": user_has_full_access(self.request.user),
                "driver_form": DriverCreateForm(prefix="driver"),
                "truck_form": TruckCreateForm(prefix="new_truck"),
            }
        )
        return context


class TruckListView(ReadAccessRequiredMixin, TemplateView):
    template_name = "relay/trucks_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "page_title": "Trucks",
                "trucks": Truck.objects.select_related("current_driver").order_by("unit_number"),
                "can_edit": user_has_full_access(self.request.user),
                "truck_form": TruckCreateForm(prefix="new_truck"),
                "driver_form": DriverCreateForm(prefix="driver"),
            }
        )
        return context


@login_required
@require_POST
@full_access_required
def driver_create_view(request):
    form = DriverCreateForm(request.POST, prefix="driver")
    if form.is_valid():
        driver = form.save()
        messages.success(request, f"Driver {driver.full_name} added.")
        return redirect("relay:board")

    messages.error(request, "Could not add driver. Check the form.")
    return _render_fleet_board(request, driver_form=form, open_modal="driver")


@login_required
@require_POST
@full_access_required
def truck_create_view(request):
    form = TruckCreateForm(request.POST, prefix="new_truck")
    if form.is_valid():
        truck = form.save(updated_by=request.user)
        messages.success(request, f"Truck {truck.unit_number} added.")
        return redirect("relay:truck_detail", pk=truck.pk)

    messages.error(request, "Could not add truck. Check the form.")
    return _render_fleet_board(request, truck_form=form, open_modal="truck")
