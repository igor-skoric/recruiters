from django.contrib import admin

from departments.models import Department, Role


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "department",
        "slug",
        "access_level",
        "can_create",
        "can_edit",
        "can_delete",
        "is_active",
    )
    list_filter = ("department", "access_level", "can_create", "can_edit", "can_delete", "is_active")
    search_fields = ("name", "slug", "department__name")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "department", "access_level", "is_active")}),
        (
            "Capabilities",
            {
                "description": "Control who can add, edit, or delete drivers/trucks. Change these per role anytime.",
                "fields": ("can_create", "can_edit", "can_delete"),
            },
        ),
    )
