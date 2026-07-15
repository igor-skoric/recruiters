from django import forms
from django.db import transaction
from django.utils import timezone

from drivers.models import Driver, DriverStatus, DriverType
from relay.models import AssignmentStatus, RelayAssignment
from relay.services import relay_service
from trucks.models import Truck, TruckStatus


def _active_drivers():
    return Driver.objects.exclude(status=DriverStatus.TERMINATED).order_by(
        "last_name", "first_name"
    )


class StartAssignmentForm(forms.Form):
    driver = forms.ModelChoiceField(
        queryset=_active_drivers(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    cycle_weeks = forms.IntegerField(
        min_value=1,
        max_value=12,
        initial=4,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 12}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, truck=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.truck = truck
        self.fields["start_date"].initial = timezone.localdate()
        self.fields["driver"].queryset = _active_drivers()


class PlanNextAssignmentForm(forms.Form):
    next_driver = forms.ModelChoiceField(
        queryset=_active_drivers(),
        label="Next driver",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    cycle_weeks = forms.IntegerField(
        min_value=1,
        max_value=12,
        initial=4,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 12}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def __init__(self, *args, truck=None, current_assignment=None, planned=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.truck = truck
        self.fields["next_driver"].queryset = _active_drivers()
        if planned:
            self.fields["next_driver"].initial = planned.driver
            self.fields["start_date"].initial = planned.start_date
            self.fields["cycle_weeks"].initial = planned.cycle_weeks
            self.fields["notes"].initial = planned.notes
        elif current_assignment:
            self.fields["start_date"].initial = current_assignment.expected_end_date


class DriverCreateForm(forms.ModelForm):
    class Meta:
        model = Driver
        fields = (
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
        self.fields["status"].initial = DriverStatus.ACTIVE
        self.fields["driver_type"].initial = DriverType.COMPANY_DRIVER
        self.fields["phone"].required = False
        self.fields["email"].required = False
        self.fields["hire_date"].required = False
        self.fields["notes"].required = False


class TruckCreateForm(forms.ModelForm):
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
        self.fields["status"].initial = TruckStatus.AVAILABLE
        for name in ("make", "model", "year", "vin", "notes"):
            self.fields[name].required = False

    @transaction.atomic
    def save(self, commit=True, updated_by=None):
        return super().save(commit=commit)
