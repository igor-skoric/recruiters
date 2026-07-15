from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from departments.models import Department, Role


class Command(BaseCommand):
    help = "Create the first IT Admin user."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Admin email address.")
        parser.add_argument("--full-name", required=True, help="Admin full name.")
        parser.add_argument("--password", required=True, help="Admin password.")

    def handle(self, *args, **options):
        try:
            department = Department.objects.get(slug="it")
            role = Role.objects.get(department=department, slug="admin")
        except (Department.DoesNotExist, Role.DoesNotExist) as exc:
            raise CommandError(
                "IT department or Admin role not found. Run seed_departments_roles first."
            ) from exc

        email = options["email"].strip().lower()
        full_name = options["full_name"].strip()
        password = options["password"]

        if User.objects.filter(email=email).exists():
            raise CommandError(f"User with email '{email}' already exists.")

        user = User.objects.create_user(
            email=email,
            password=password,
            full_name=full_name,
            department=department,
            role=role,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Created IT Admin user: {user.full_name} ({user.email})"
            )
        )
