from datetime import timedelta

from django import forms
from django.db import transaction
from django.utils import timezone

from drivers.models import Driver, DriverStatus, DriverType
from relay.services import relay_service
from trucks.models import Truck, TruckStatus


def _active_drivers():
    return Driver.objects.exclude(status=DriverStatus.TERMINATED).order_by(
        "last_name", "first_name"
    )


def _driver_choice_label(driver: Driver) -> str:
    if driver.driver_id:
        return f"{driver.full_name} ({driver.driver_id})"
    return driver.full_name


class StartAssignmentForm(forms.Form):
    driver = forms.ModelChoiceField(
        queryset=_active_drivers(),
        widget=forms.Select(
            attrs={
                "class": "form-control",
                "data-searchable-select": "1",
                "data-search-placeholder": "Search by name or Driver ID…",
            }
        ),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-start": "1",
            }
        ),
    )
    home_time_date = forms.DateField(
        label="Return home / truck free date",
        help_text="Date when the driver goes home (end of OTR cycle).",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-end": "1",
            }
        ),
    )
    back_to_work_date = forms.DateField(
        label="Back to work date",
        help_text="First day the driver is available again after home time.",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, truck=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.truck = truck
        today = timezone.localdate()
        home = relay_service.calculate_expected_end_date(today)
        self.fields["start_date"].initial = today
        self.fields["home_time_date"].initial = home
        self.fields["back_to_work_date"].initial = home + timedelta(
            days=relay_service.DEFAULT_HOME_TIME_DAYS
        )
        self.fields["driver"].queryset = _active_drivers()
        self.fields["driver"].label_from_instance = _driver_choice_label

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        home = cleaned.get("home_time_date")
        back = cleaned.get("back_to_work_date")
        if start and home:
            if home <= start:
                self.add_error("home_time_date", "Return date must be after start date.")
            else:
                cleaned["cycle_weeks"] = relay_service.cycle_weeks_from_dates(start, home)
                cleaned["cycle_duration_label"] = relay_service.format_cycle_duration(start, home)
        if home and back:
            if back <= home:
                self.add_error(
                    "back_to_work_date",
                    "Back to work date must be after return home date.",
                )
            else:
                days = (back - home).days
                if days > 60:
                    self.add_error(
                        "back_to_work_date",
                        "Home time duration must be between 1 and 60 days.",
                    )
                else:
                    cleaned["home_time_days"] = days
        elif home:
            cleaned["home_time_days"] = relay_service.DEFAULT_HOME_TIME_DAYS
        return cleaned


class PlanNextAssignmentForm(forms.Form):
    next_driver = forms.ModelChoiceField(
        queryset=_active_drivers(),
        label="Next driver",
        widget=forms.Select(
            attrs={
                "class": "form-control",
                "data-searchable-select": "1",
                "data-search-placeholder": "Search by name or Driver ID…",
            }
        ),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-start": "1",
            }
        ),
    )
    home_time_date = forms.DateField(
        label="Return home / truck free date",
        help_text="Date when the next driver goes home.",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-end": "1",
            }
        ),
    )
    back_to_work_date = forms.DateField(
        label="Back to work date",
        help_text="First day the next driver is available again after home time.",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, truck=None, current_assignment=None, planned=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.truck = truck
        self.fields["next_driver"].queryset = _active_drivers()
        self.fields["next_driver"].label_from_instance = _driver_choice_label
        if planned:
            self.fields["next_driver"].initial = planned.driver
            self.fields["start_date"].initial = planned.start_date
            self.fields["home_time_date"].initial = planned.expected_end_date
            self.fields["back_to_work_date"].initial = (
                planned.expected_end_date + timedelta(days=planned.home_time_days)
            )
            self.fields["notes"].initial = planned.notes
        elif current_assignment:
            start = current_assignment.expected_end_date
            home = relay_service.calculate_expected_end_date(start)
            self.fields["start_date"].initial = start
            self.fields["home_time_date"].initial = home
            self.fields["back_to_work_date"].initial = home + timedelta(
                days=relay_service.DEFAULT_HOME_TIME_DAYS
            )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        home = cleaned.get("home_time_date")
        back = cleaned.get("back_to_work_date")
        if start and home:
            if home <= start:
                self.add_error("home_time_date", "Return date must be after start date.")
            else:
                cleaned["cycle_weeks"] = relay_service.cycle_weeks_from_dates(start, home)
                cleaned["cycle_duration_label"] = relay_service.format_cycle_duration(start, home)
        if home and back:
            if back <= home:
                self.add_error(
                    "back_to_work_date",
                    "Back to work date must be after return home date.",
                )
            else:
                days = (back - home).days
                if days > 60:
                    self.add_error(
                        "back_to_work_date",
                        "Home time duration must be between 1 and 60 days.",
                    )
                else:
                    cleaned["home_time_days"] = days
        elif home:
            cleaned["home_time_days"] = relay_service.DEFAULT_HOME_TIME_DAYS
        return cleaned


class UpdateHomeTimeForm(forms.Form):
    start_date = forms.DateField(
        label="Start date",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-start": "1",
            }
        ),
    )
    home_time_date = forms.DateField(
        label="Return home / truck free date",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "data-cycle-end": "1",
            }
        ),
    )
    back_to_work_date = forms.DateField(
        label="Back to work date",
        help_text="First day the driver is available again.",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def __init__(self, *args, assignment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment
        if assignment:
            self.fields["start_date"].initial = assignment.start_date
            self.fields["home_time_date"].initial = assignment.expected_end_date
            self.fields["back_to_work_date"].initial = (
                assignment.expected_end_date + timedelta(days=assignment.home_time_days)
            )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        home = cleaned.get("home_time_date")
        back = cleaned.get("back_to_work_date")
        if start and home and home <= start:
            self.add_error(
                "home_time_date",
                "Return date must be after start date.",
            )
        if home and back:
            if back <= home:
                self.add_error(
                    "back_to_work_date",
                    "Back to work date must be after return home date.",
                )
            else:
                home_time_days = (back - home).days
                if home_time_days > 60:
                    self.add_error(
                        "back_to_work_date",
                        "Home time duration must be between 1 and 60 days.",
                    )
                else:
                    cleaned["home_time_days"] = home_time_days
        return cleaned


class UpdateDriverHomeTimePeriodForm(forms.Form):
    end_date = forms.DateField(
        label="Home time end date",
        help_text="First day the driver is available again (exclusive end of home time).",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def __init__(self, *args, period=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        if period and period.end_date:
            self.fields["end_date"].initial = period.end_date
        elif period:
            self.fields["end_date"].initial = period.start_date + timedelta(days=7)

    def clean_end_date(self):
        end = self.cleaned_data["end_date"]
        if self.period and end <= self.period.start_date:
            raise forms.ValidationError("Home time end date must be after start date.")
        if self.period:
            days = (end - self.period.start_date).days
            if days < 1 or days > 60:
                raise forms.ValidationError("Home time duration must be between 1 and 60 days.")
        return end


class DriverCreateForm(forms.ModelForm):
    class Meta:
        model = Driver
        fields = (
            "driver_id",
            "first_name",
            "last_name",
            "phone",
            "email",
            "status",
            "driver_type",
            "hire_date",
            "notes",
        )
        widgets = {
            "driver_id": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "autocomplete": "off",
                    "placeholder": "Optional Driver ID",
                }
            ),
            "first_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "driver_type": forms.Select(attrs={"class": "form-control"}),
            "hire_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["status"].initial = DriverStatus.ACTIVE
            self.fields["driver_type"].initial = DriverType.COMPANY_DRIVER
        self.fields["driver_id"].required = False
        self.fields["phone"].required = False
        self.fields["email"].required = False
        self.fields["hire_date"].required = False
        self.fields["notes"].required = False

    def clean_driver_id(self):
        value = self.cleaned_data.get("driver_id")
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class TruckCreateForm(forms.ModelForm):
    driver_id = forms.CharField(
        label="Driver ID",
        required=False,
        help_text="Optional. Match an existing driver and set as current driver.",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "placeholder": "Driver ID",
            }
        ),
    )

    class Meta:
        model = Truck
        fields = (
            "unit_number",
            "make",
            "model",
            "year",
            "status",
            "vin",
            "notes",
        )
        widgets = {
            "unit_number": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "make": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "model": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "year": forms.NumberInput(attrs={"class": "form-control", "min": 1990, "max": 2100}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "vin": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["status"].initial = TruckStatus.AVAILABLE
        elif self.instance.current_driver_id and self.instance.current_driver.driver_id:
            self.fields["driver_id"].initial = self.instance.current_driver.driver_id
        for name in ("make", "model", "year", "vin", "notes", "driver_id"):
            self.fields[name].required = False

    def clean_driver_id(self):
        value = (self.cleaned_data.get("driver_id") or "").strip()
        return value or None

    def clean(self):
        cleaned = super().clean()
        driver_ref = cleaned.get("driver_id")
        if driver_ref:
            driver = Driver.objects.filter(driver_id__iexact=driver_ref).order_by("id").first()
            if not driver:
                self.add_error(
                    "driver_id",
                    f"No driver found with Driver ID '{driver_ref}'.",
                )
            else:
                cleaned["resolved_current_driver"] = driver
        else:
            cleaned["resolved_current_driver"] = None
        return cleaned

    @transaction.atomic
    def save(self, commit=True, updated_by=None):
        # current_driver is relay-owned cache; truck forms never write it.
        truck = super().save(commit=False)
        if commit:
            truck.save()
        return truck


class SpreadsheetImportForm(forms.Form):
    file = forms.FileField(
        label="CSV or Excel file",
        help_text="Upload a .csv or .xlsx file. Max 2,000 rows.",
        widget=forms.FileInput(
            attrs={
                "class": "form-control",
                "accept": ".csv,.xlsx,.xlsm,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
        ),
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not name.endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError("Use a .csv or .xlsx file.")
        if uploaded.size and uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("File is too large (max 5 MB).")
        return uploaded
