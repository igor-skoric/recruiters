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
        help_text="Legacy coarse level. Prefer the capability flags below for new rules.",
    )
    can_create = models.BooleanField(
        "Can create",
        default=False,
        help_text="Add and import drivers/trucks.",
    )
    can_edit = models.BooleanField(
        "Can edit",
        default=False,
        help_text="Edit drivers/trucks and manage relay assignments.",
    )
    can_delete = models.BooleanField(
        "Can delete",
        default=False,
        help_text="Delete drivers and trucks.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["department__name", "name"]
        unique_together = [["department", "slug"]]

    def __str__(self) -> str:
        return f"{self.department.name} / {self.name}"

    def save(self, *args, **kwargs):
        # Keep FULL roles useful out of the box when created from seed/admin
        # without manually ticking every box — only auto-fill on brand-new FULL roles.
        if self._state.adding and self.access_level == AccessLevel.FULL:
            if not self.can_create and not self.can_edit and not self.can_delete:
                self.can_create = True
                self.can_edit = True
                self.can_delete = True
        super().save(*args, **kwargs)

    @property
    def access_level_label(self) -> str:
        return self.get_access_level_display()
