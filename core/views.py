from django.views.generic import TemplateView

from accounts.permissions import ReadAccessRequiredMixin


class DashboardView(ReadAccessRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update(
            {
                "page_title": "Dashboard",
                "user_full_name": user.full_name,
                "user_department": user.department.name,
                "user_role": user.role.name,
                "user_access_level": user.access_level_label,
            }
        )
        return context
