from django.contrib import admin

from companies.models import CompanyData


@admin.register(CompanyData)
class CompanyDataAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "protransport_id",
        "dba",
        "mc",
        "us_dot_no",
        "phone",
        "email",
        "mailing_city",
        "mailing_state",
        "last_synced_at",
    )
    search_fields = ("name", "dba", "mc", "us_dot_no", "protransport_id", "email")
    readonly_fields = ("last_synced_at", "protransport_id")
    list_per_page = 50
    show_full_result_count = False
