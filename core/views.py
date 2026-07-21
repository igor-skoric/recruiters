from django.views.generic import TemplateView

from accounts.permissions import ReadAccessRequiredMixin
from drivers.models import Driver, DriverStatus
from relay.services import relay_service


class DashboardView(ReadAccessRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        board_rows = relay_service.get_relay_board()
        fleet_summary = relay_service.get_fleet_summary(board_rows)
        cycles_ending_soon = sum(
            1 for row in board_rows if row.days_left is not None and 0 <= row.days_left <= 7
        )
        context.update(
            {
                "page_title": "Dashboard",
                "user_full_name": user.full_name,
                "user_department": user.department.name,
                "user_role": user.role.name,
                "user_access_level": user.access_level_label,
                "fleet_summary": fleet_summary,
                "drivers_home_time": Driver.objects.filter(status=DriverStatus.HOME_TIME).count(),
                "cycles_ending_soon": cycles_ending_soon,
            }
        )
        return context
