from django.db import models


class AccessLevel(models.TextChoices):
    READ = "read", "Read Only"
    FULL = "full", "Full Access"


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Role(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    access_level = models.CharField(
        max_length=10,
        choices=AccessLevel.choices,
        default=AccessLevel.READ,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["department__name", "name"]
        unique_together = [["department", "slug"]]

    def __str__(self) -> str:
        return f"{self.department.name} / {self.name}"

    @property
    def access_level_label(self) -> str:
        return self.get_access_level_display()
