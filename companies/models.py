from django.db import models


class CompanyData(models.Model):
    """
    Pro Transport company_data / division (master data).

    Stable key: protransport_id = company_data.id
    Linked from Driver/Truck via division_id in PT.
    """

    protransport_id = models.CharField(
        "Pro Transport ID",
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Pro Transport company_data.id (stable external key).",
    )
    name = models.CharField(
        max_length=200,
        help_text="Division / company name (PT division_name).",
    )
    dba = models.CharField("DBA", max_length=200, blank=True)
    mc = models.CharField("MC", max_length=50, blank=True)
    us_dot_no = models.CharField("US DOT", max_length=50, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=200, blank=True)
    mailing_city = models.CharField(max_length=100, blank=True)
    mailing_state = models.CharField(max_length=50, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company / division"
        verbose_name_plural = "Companies / divisions"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name or f"Division {self.protransport_id}"

    def save(self, *args, **kwargs):
        if self.protransport_id is not None:
            self.protransport_id = str(self.protransport_id).strip()
        super().save(*args, **kwargs)
