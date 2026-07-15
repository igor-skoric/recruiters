from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

from accounts.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    department = models.ForeignKey(
        "departments.Department",
        on_delete=models.PROTECT,
        related_name="users",
    )
    role = models.ForeignKey(
        "departments.Role",
        on_delete=models.PROTECT,
        related_name="users",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["full_name", "email"]

    def __str__(self) -> str:
        return self.full_name

    def clean(self) -> None:
        super().clean()
        if self.role_id and self.department_id:
            if self.role.department_id != self.department_id:
                raise ValidationError(
                    {"role": "Role must belong to the user's department."}
                )

    @property
    def access_level(self) -> str:
        if self.is_superuser:
            return "full"
        return self.role.access_level

    @property
    def access_level_label(self) -> str:
        if self.is_superuser:
            return "Full Access"
        return self.role.access_level_label
