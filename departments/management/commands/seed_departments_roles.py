from django.core.management.base import BaseCommand

from departments.models import AccessLevel, Department, Role

INITIAL_DATA = [
    {
        "department": {"name": "IT", "slug": "it"},
        "role": {
            "name": "Admin",
            "slug": "admin",
            "access_level": AccessLevel.FULL,
            "can_create": True,
            "can_edit": True,
            "can_delete": True,
        },
    },
    {
        "department": {"name": "Recruiting", "slug": "recruiting"},
        "role": {
            "name": "Read Only",
            "slug": "read-only",
            "access_level": AccessLevel.READ,
            "can_create": False,
            "can_edit": False,
            "can_delete": False,
        },
    },
]


class Command(BaseCommand):
    help = "Seed initial departments and roles."

    def handle(self, *args, **options):
        created_departments = 0
        created_roles = 0

        for entry in INITIAL_DATA:
            department_data = entry["department"]
            role_data = entry["role"]

            department, department_created = Department.objects.get_or_create(
                slug=department_data["slug"],
                defaults={
                    "name": department_data["name"],
                    "is_active": True,
                },
            )
            if department_created:
                created_departments += 1
                self.stdout.write(self.style.SUCCESS(f"Created department: {department.name}"))
            else:
                self.stdout.write(f"Department already exists: {department.name}")

            role, role_created = Role.objects.get_or_create(
                department=department,
                slug=role_data["slug"],
                defaults={
                    "name": role_data["name"],
                    "access_level": role_data["access_level"],
                    "can_create": role_data.get("can_create", False),
                    "can_edit": role_data.get("can_edit", False),
                    "can_delete": role_data.get("can_delete", False),
                    "is_active": True,
                },
            )
            if role_created:
                created_roles += 1
                self.stdout.write(self.style.SUCCESS(f"Created role: {role}"))
            else:
                self.stdout.write(f"Role already exists: {role}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete. Departments created: {created_departments}, "
                f"roles created: {created_roles}."
            )
        )
