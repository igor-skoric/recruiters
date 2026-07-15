from typing import Any

from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    def _default_superuser_department_role(self):
        from departments.models import Department, Role

        try:
            department = Department.objects.get(slug="it")
            role = Role.objects.get(department=department, slug="admin")
        except (Department.DoesNotExist, Role.DoesNotExist) as exc:
            raise ValueError(
                "IT department or Admin role not found. Run seed_departments_roles first."
            ) from exc
        return department, role

    def create_user(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        if not extra_fields.get("department") or not extra_fields.get("role"):
            department, role = self._default_superuser_department_role()
            extra_fields.setdefault("department", department)
            extra_fields.setdefault("role", role)

        return self.create_user(email, password, **extra_fields)
