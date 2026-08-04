from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import timezone

from companies.models import CompanyData
from drivers.models import Driver, DriverStatus, DriverType, EmploymentStatus
from relay.models import AssignmentStatus, DriverPeriodStatus, DriverStatusPeriod, RelayAssignment
from relay.services import relay_service
from sync.management.commands.rebuild_current_driver_cache import rebuild_current_driver_cache
from sync.services import bootstrap as bootstrap_svc
from sync.services import master_sync
from sync.services import pro_transport_mapping as mapping
from trucks.models import Truck, TruckStatus


class MappingTests(TestCase):
    def test_map_employment_status(self):
        self.assertEqual(mapping.map_employment_status("ACTIVE"), EmploymentStatus.ACTIVE)
        self.assertEqual(
            mapping.map_employment_status("TERMINATED"), EmploymentStatus.TERMINATED
        )

    def test_clean_str_preserves_zero_id(self):
        self.assertEqual(mapping.clean_str(0, 64), "0")

    def test_map_company_master_row(self):
        data = mapping.map_company_master_row(
            {
                "id": 0,
                "division_name": "GNS Trucking Inc.",
                "dba": "",
                "mc": "956641",
                "us_dot_no": "2857294 ",
                "phone_no": "(708)722-1166 ",
                "email_address": "pod@drivegns.com",
                "website": "",
                "ma_city": "Chicago",
                "ma_state": "IL",
            }
        )
        self.assertEqual(data["protransport_id"], "0")
        self.assertEqual(data["name"], "GNS Trucking Inc.")
        self.assertEqual(data["us_dot_no"], "2857294")
        self.assertEqual(data["mailing_city"], "Chicago")

    def test_map_driver_includes_division_pt_id(self):
        data = mapping.map_driver_master_row(
            {
                "id": 10,
                "first_name": "A",
                "last_name": "B",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "ACTIVE",
                "is_company_driver": True,
                "division_id": 0,
            }
        )
        self.assertEqual(data["division_pt_id"], "0")

    def test_map_driver_master_row(self):
        data = mapping.map_driver_master_row(
            {
                "id": 1439,
                "first_name": "WILLIAM ",
                "last_name": "SIBLEY",
                "phone_cell": "",
                "phone_home": "(386)227-0552 ",
                "email": "wsibley2013@gmail.com",
                "hire_date": "2025-12-04 00:00:00",
                "status": "TERMINATED",
                "is_company_driver": True,
            }
        )
        self.assertEqual(data["driver_id"], "1439")
        self.assertEqual(data["employment_status"], EmploymentStatus.TERMINATED)
        self.assertNotIn("status", data)
        self.assertNotIn("notes", data)

    def test_map_truck_master_row_skips_vin(self):
        data = mapping.map_truck_master_row(
            {
                "id": 21,
                "unit_no": "563041",
                "make": "FRHT",
                "model": "",
                "year_of_truck": "2022-01-01 00:00:00",
                "is_active": False,
                "status": "",
                "vin_no": "ciphertext",
            }
        )
        self.assertEqual(data["protransport_id"], "21")
        self.assertEqual(data["year"], 2022)
        self.assertFalse(data["source_is_active"])
        self.assertNotIn("vin", data)
        self.assertNotIn("status", data)


class MasterSyncUpsertTests(TestCase):
    def test_upsert_drivers_by_pt_id_and_rename(self):
        rows = [
            {
                "id": "100",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "phone_cell": "111",
                "phone_home": "",
                "email": "ada@example.com",
                "hire_date": "2024-01-15 00:00:00",
                "status": "ACTIVE",
                "is_company_driver": True,
            }
        ]
        first = master_sync.upsert_drivers_from_rows(rows)
        self.assertEqual(first.created, 1)
        rows[0]["first_name"] = "Augusta"
        rows[0]["last_name"] = "Byron"
        second = master_sync.upsert_drivers_from_rows(rows)
        self.assertEqual(second.updated, 1)
        self.assertEqual(Driver.objects.filter(driver_id="100").count(), 1)
        driver = Driver.objects.get(driver_id="100")
        self.assertEqual(driver.first_name, "Augusta")
        self.assertEqual(driver.last_name, "Byron")

    def test_upsert_drivers_skips_non_active_and_owner_operators(self):
        rows = [
            {
                "id": "200",
                "first_name": "Term",
                "last_name": "Inated",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "TERMINATED",
                "is_company_driver": True,
            },
            {
                "id": "201",
                "first_name": "Un",
                "last_name": "Known",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "UNKNOWN",
                "is_company_driver": True,
            },
            {
                "id": "202",
                "first_name": "In",
                "last_name": "Active",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "INACTIVE",
                "is_company_driver": True,
            },
            {
                "id": "203",
                "first_name": "Own",
                "last_name": "Er",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "ACTIVE",
                "is_company_driver": False,
            },
            {
                "id": "204",
                "first_name": "Ok",
                "last_name": "Company",
                "phone_cell": "",
                "phone_home": "",
                "email": "",
                "hire_date": None,
                "status": "ACTIVE",
                "is_company_driver": True,
            },
        ]
        result = master_sync.upsert_drivers_from_rows(rows)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 4)
        self.assertTrue(Driver.objects.filter(driver_id="204").exists())
        self.assertFalse(Driver.objects.filter(driver_id="200").exists())
        self.assertFalse(Driver.objects.filter(driver_id="201").exists())
        self.assertFalse(Driver.objects.filter(driver_id="202").exists())
        self.assertFalse(Driver.objects.filter(driver_id="203").exists())

    def test_upsert_drivers_still_updates_existing_when_no_longer_allowed(self):
        Driver.objects.create(
            driver_id="300",
            first_name="Was",
            last_name="Active",
            employment_status=EmploymentStatus.ACTIVE,
            driver_type=DriverType.COMPANY_DRIVER,
        )
        result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "300",
                    "first_name": "Now",
                    "last_name": "Terminated",
                    "phone_cell": "9",
                    "phone_home": "",
                    "email": "",
                    "hire_date": None,
                    "status": "TERMINATED",
                    "is_company_driver": True,
                }
            ]
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped, 0)
        driver = Driver.objects.get(driver_id="300")
        self.assertEqual(driver.first_name, "Now")
        self.assertEqual(driver.employment_status, EmploymentStatus.TERMINATED)
        self.assertEqual(driver.status, DriverStatus.TERMINATED)

    def test_upsert_drivers_create_sets_ops_active(self):
        result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "301",
                    "first_name": "New",
                    "last_name": "Hire",
                    "phone_cell": "",
                    "phone_home": "",
                    "email": "",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                }
            ]
        )
        self.assertEqual(result.created, 1)
        driver = Driver.objects.get(driver_id="301")
        self.assertEqual(driver.status, DriverStatus.ACTIVE)
        self.assertEqual(driver.employment_status, EmploymentStatus.ACTIVE)

    def test_upsert_drivers_active_employment_does_not_clear_home_time(self):
        Driver.objects.create(
            driver_id="302",
            first_name="Home",
            last_name="Time",
            employment_status=EmploymentStatus.ACTIVE,
            status=DriverStatus.HOME_TIME,
            driver_type=DriverType.COMPANY_DRIVER,
        )
        result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "302",
                    "first_name": "Home",
                    "last_name": "Time",
                    "phone_cell": "1",
                    "phone_home": "",
                    "email": "",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                }
            ]
        )
        self.assertEqual(result.updated, 1)
        driver = Driver.objects.get(driver_id="302")
        self.assertEqual(driver.status, DriverStatus.HOME_TIME)

    def test_upsert_trucks_by_protransport_id_unit_change(self):
        Truck.objects.create(protransport_id="21", unit_number="OLD-1", make="OLD")
        result = master_sync.upsert_trucks_from_rows(
            [
                {
                    "id": "21",
                    "unit_no": "NEW-1",
                    "make": "FRHT",
                    "model": "Cascadia",
                    "year_of_truck": datetime(2022, 1, 1),
                    "is_active": True,
                    "status": "ACTIVE",
                }
            ]
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(Truck.objects.count(), 1)
        truck = Truck.objects.get(protransport_id="21")
        self.assertEqual(truck.unit_number, "NEW-1")
        self.assertEqual(truck.make, "FRHT")

    def test_upsert_trucks_skips_inactive_on_create(self):
        result = master_sync.upsert_trucks_from_rows(
            [
                {
                    "id": "90",
                    "unit_no": "DEAD-1",
                    "make": "OLD",
                    "model": "X",
                    "year_of_truck": None,
                    "is_active": False,
                    "status": "INACTIVE",
                },
                {
                    "id": "91",
                    "unit_no": "LOSS-1",
                    "make": "OLD",
                    "model": "Y",
                    "year_of_truck": None,
                    "is_active": True,
                    "status": "total loss",
                },
                {
                    "id": "92",
                    "unit_no": "OK-1",
                    "make": "VOLVO",
                    "model": "VNL",
                    "year_of_truck": "2020-01-01",
                    "is_active": True,
                    "status": "ACTIVE",
                },
            ]
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 2)
        self.assertTrue(Truck.objects.filter(protransport_id="92").exists())
        self.assertFalse(Truck.objects.filter(protransport_id="90").exists())
        self.assertFalse(Truck.objects.filter(protransport_id="91").exists())

    def test_upsert_trucks_still_updates_existing_inactive(self):
        Truck.objects.create(
            protransport_id="93",
            unit_number="WAS-OK",
            source_is_active=True,
        )
        result = master_sync.upsert_trucks_from_rows(
            [
                {
                    "id": "93",
                    "unit_no": "WAS-OK",
                    "make": "FRHT",
                    "model": "X",
                    "year_of_truck": None,
                    "is_active": False,
                    "status": "INACTIVE",
                }
            ]
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped, 0)
        truck = Truck.objects.get(protransport_id="93")
        self.assertFalse(truck.source_is_active)
        self.assertEqual(truck.make, "FRHT")

    def test_master_sync_does_not_touch_planning(self):
        driver = Driver.objects.create(
            driver_id="100",
            first_name="Ada",
            last_name="Lovelace",
            status=DriverStatus.HOME_TIME,
        )
        truck = Truck.objects.create(
            protransport_id="21",
            unit_number="T-1",
            status=TruckStatus.OTR,
            current_driver=driver,
        )
        assignment = relay_service.create_assignment(
            driver=driver,
            truck=truck,
            start_date=timezone.localdate() - timedelta(days=7),
            status=AssignmentStatus.ACTIVE,
        )
        # Force operational state after create (create sets active).
        driver.status = DriverStatus.HOME_TIME
        driver.save(update_fields=["status"])
        period = DriverStatusPeriod.objects.create(
            driver=driver,
            status=DriverPeriodStatus.HOME_TIME,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=7),
            assignment=assignment,
        )
        before_assignment = assignment.pk
        before_period = period.pk
        before_driver_cache = truck.current_driver_id

        master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "100",
                    "first_name": "Ada",
                    "last_name": "Updated",
                    "phone_cell": "9",
                    "phone_home": "",
                    "email": "a@example.com",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                }
            ]
        )
        master_sync.upsert_trucks_from_rows(
            [
                {
                    "id": "21",
                    "unit_no": "T-1",
                    "make": "VOLVO",
                    "model": "VNL",
                    "year_of_truck": "2020-01-01 00:00:00",
                    "is_active": False,
                    "status": "total loss",
                }
            ]
        )

        driver.refresh_from_db()
        truck.refresh_from_db()
        assignment.refresh_from_db()
        period.refresh_from_db()
        self.assertEqual(driver.last_name, "Updated")
        self.assertEqual(driver.employment_status, EmploymentStatus.ACTIVE)
        self.assertEqual(driver.status, DriverStatus.HOME_TIME)
        self.assertEqual(truck.status, TruckStatus.OTR)
        self.assertFalse(truck.source_is_active)
        self.assertEqual(truck.current_driver_id, before_driver_cache)
        self.assertEqual(assignment.pk, before_assignment)
        self.assertEqual(period.pk, before_period)
        self.assertEqual(
            RelayAssignment.objects.filter(pk=before_assignment).count(), 1
        )


class BootstrapTests(TestCase):
    def setUp(self):
        self.driver = Driver.objects.create(
            driver_id="100", first_name="A", last_name="Driver"
        )
        self.truck = Truck.objects.create(
            protransport_id="21", unit_number="T-21"
        )
        self.row = {
            "id": "21",
            "unit_no": "T-21",
            "driver1_id": "100",
            "make": "",
            "model": "",
            "year_of_truck": None,
            "is_active": True,
            "status": "",
        }

    def test_bootstrap_creates_active_assignment(self):
        result = bootstrap_svc.bootstrap_rows(
            [self.row],
            default_start_date=date(2026, 7, 1),
            dry_run=False,
        )
        self.assertEqual(result.assignments_created, 1)
        assignment = RelayAssignment.objects.get(truck=self.truck)
        self.assertEqual(assignment.status, AssignmentStatus.ACTIVE)
        self.assertEqual(assignment.driver_id, self.driver.pk)
        self.truck.refresh_from_db()
        self.assertEqual(self.truck.current_driver_id, self.driver.pk)
        self.assertIn("estimated", assignment.notes.lower())
        self.assertTrue(assignment.start_date_is_estimated)

    def test_bootstrap_idempotent(self):
        bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        second = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        self.assertEqual(second.already_matched, 1)
        self.assertEqual(RelayAssignment.objects.filter(truck=self.truck).count(), 1)

    def test_bootstrap_skips_existing_planning(self):
        other = Driver.objects.create(driver_id="200", first_name="B", last_name="Other")
        relay_service.create_assignment(
            driver=other,
            truck=self.truck,
            start_date=date(2026, 6, 1),
            status=AssignmentStatus.ACTIVE,
        )
        result = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        self.assertEqual(result.skipped_existing_planning, 1)
        self.assertEqual(RelayAssignment.objects.filter(truck=self.truck).count(), 1)
        self.assertEqual(
            RelayAssignment.objects.get(truck=self.truck).driver_id, other.pk
        )

    def test_bootstrap_dry_run_no_write(self):
        result = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=True
        )
        self.assertEqual(result.assignments_created, 1)
        self.assertFalse(RelayAssignment.objects.exists())

    def test_bootstrap_missing_start_date(self):
        result = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=None, dry_run=False
        )
        self.assertEqual(result.missing_start_date, 1)
        self.assertFalse(RelayAssignment.objects.exists())

    def test_bootstrap_matches_only_protransport_ids(self):
        # Same unit number but wrong/missing PT id must NOT match.
        Truck.objects.filter(pk=self.truck.pk).update(protransport_id="999")
        result = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        self.assertEqual(result.missing_truck, 1)
        self.assertFalse(RelayAssignment.objects.exists())

    def test_bootstrap_skips_duplicate_pt_current_driver(self):
        other_truck = Truck.objects.create(protransport_id="22", unit_number="T-22")
        rows = [
            self.row,
            {
                "id": "22",
                "unit_no": "T-22",
                "driver1_id": "100",  # same driver on two trucks
                "make": "",
                "model": "",
                "year_of_truck": None,
                "is_active": True,
                "status": "",
            },
        ]
        result = bootstrap_svc.bootstrap_rows(
            rows, default_start_date=date(2026, 7, 1), dry_run=False
        )
        self.assertEqual(result.driver_current_on_multiple_trucks, 1)
        self.assertEqual(result.ambiguous_relation, 2)
        self.assertFalse(RelayAssignment.objects.exists())
        self.assertTrue(Truck.objects.filter(pk=other_truck.pk).exists())

    def test_bootstrap_skips_inactive_pt_driver_relation(self):
        self.driver.employment_status = EmploymentStatus.TERMINATED
        self.driver.save(update_fields=["employment_status"])
        result = bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        self.assertEqual(result.inactive_pt_driver_with_relation, 1)
        self.assertFalse(RelayAssignment.objects.exists())

    def test_ui_date_edit_clears_estimated_flag(self):
        bootstrap_svc.bootstrap_rows(
            [self.row], default_start_date=date(2026, 7, 1), dry_run=False
        )
        assignment = RelayAssignment.objects.get(truck=self.truck)
        self.assertTrue(assignment.start_date_is_estimated)
        relay_service.update_assignment_home_time(
            assignment,
            home_time_date=date(2026, 8, 1),
            start_date=date(2026, 7, 5),
        )
        assignment.refresh_from_db()
        self.assertFalse(assignment.start_date_is_estimated)


class CompanySyncTests(TestCase):
    def test_upsert_companies_and_link_driver_division(self):
        company_rows = [
            {
                "id": 3,
                "division_name": "Ocean 7 Logistics Inc",
                "dba": "",
                "mc": "1331169",
                "us_dot_no": "3750659",
                "phone_no": "(630)318-0802",
                "email_address": "",
                "website": "",
                "ma_city": "",
                "ma_state": "",
            }
        ]
        result = master_sync.upsert_companies_from_rows(company_rows)
        self.assertEqual(result.created, 1)
        company = CompanyData.objects.get(protransport_id="3")
        self.assertEqual(company.name, "Ocean 7 Logistics Inc")

        driver_result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "55",
                    "first_name": "Pat",
                    "last_name": "Driver",
                    "phone_cell": "1",
                    "phone_home": "",
                    "email": "p@example.com",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                    "division_id": 3,
                }
            ]
        )
        self.assertEqual(driver_result.created, 1)
        driver = Driver.objects.get(driver_id="55")
        self.assertEqual(driver.division_id, company.pk)

    def test_missing_division_warns_and_leaves_null(self):
        result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "77",
                    "first_name": "No",
                    "last_name": "Div",
                    "phone_cell": "",
                    "phone_home": "",
                    "email": "",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                    "division_id": 99,
                }
            ]
        )
        self.assertEqual(result.created, 1)
        self.assertTrue(any("division_id=99" in w for w in result.warnings))
        self.assertIsNone(Driver.objects.get(driver_id="77").division_id)


class MasterSyncSafetyTests(TestCase):
    def test_master_sync_dry_run_does_not_write(self):
        Driver.objects.create(
            driver_id="100",
            first_name="Ada",
            last_name="Lovelace",
            employment_status=EmploymentStatus.ACTIVE,
        )
        before = Driver.objects.get(driver_id="100").last_synced_at
        result = master_sync.upsert_drivers_from_rows(
            [
                {
                    "id": "100",
                    "first_name": "Augusta",
                    "last_name": "Byron",
                    "phone_cell": "1",
                    "phone_home": "",
                    "email": "a@example.com",
                    "hire_date": None,
                    "status": "ACTIVE",
                    "is_company_driver": True,
                }
            ],
            dry_run=True,
        )
        self.assertEqual(result.updated, 1)
        driver = Driver.objects.get(driver_id="100")
        self.assertEqual(driver.first_name, "Ada")
        self.assertEqual(driver.last_synced_at, before)

    def test_inactive_source_warns_but_keeps_assignment(self):
        driver = Driver.objects.create(
            driver_id="100", first_name="A", last_name="B", status=DriverStatus.ACTIVE
        )
        truck = Truck.objects.create(
            protransport_id="21",
            unit_number="T-1",
            status=TruckStatus.OTR,
            source_is_active=True,
        )
        assignment = relay_service.create_assignment(
            driver=driver,
            truck=truck,
            start_date=timezone.localdate() - timedelta(days=3),
            status=AssignmentStatus.ACTIVE,
        )
        result = master_sync.upsert_trucks_from_rows(
            [
                {
                    "id": "21",
                    "unit_no": "T-1",
                    "make": "VOLVO",
                    "model": "",
                    "year_of_truck": "2020-01-01 00:00:00",
                    "is_active": False,
                    "status": "total loss",
                }
            ],
            dry_run=False,
        )
        self.assertTrue(any("ACTIVE RelayAssignment" in w for w in result.warnings))
        assignment.refresh_from_db()
        truck.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.ACTIVE)
        self.assertEqual(truck.status, TruckStatus.OTR)
        self.assertFalse(truck.source_is_active)
        self.assertEqual(truck.current_driver_id, driver.pk)


class AuditIntegrityTests(TestCase):
    def test_audit_finds_cache_mismatch(self):
        from sync.management.commands.audit_fleet_integrity import audit_fleet_integrity

        driver = Driver.objects.create(driver_id="1", first_name="A", last_name="B")
        other = Driver.objects.create(driver_id="2", first_name="C", last_name="D")
        truck = Truck.objects.create(unit_number="T-1", protransport_id="9")
        relay_service.create_assignment(
            driver=driver,
            truck=truck,
            start_date=timezone.localdate() - timedelta(days=2),
            status=AssignmentStatus.ACTIVE,
        )
        truck.current_driver = other
        truck.save(update_fields=["current_driver"])
        report = audit_fleet_integrity()
        self.assertTrue(any("cache mismatch" in issue for issue in report.issues))


class RebuildCacheTests(TestCase):
    def test_rebuild_current_driver_cache(self):
        driver = Driver.objects.create(driver_id="1", first_name="A", last_name="B")
        truck = Truck.objects.create(unit_number="T-1", protransport_id="9")
        relay_service.create_assignment(
            driver=driver,
            truck=truck,
            start_date=timezone.localdate() - timedelta(days=3),
            status=AssignmentStatus.ACTIVE,
        )
        truck.current_driver = None
        truck.save(update_fields=["current_driver"])
        result = rebuild_current_driver_cache(dry_run=False)
        truck.refresh_from_db()
        self.assertEqual(result.set_from_active, 1)
        self.assertEqual(truck.current_driver_id, driver.pk)


class CompatTests(TestCase):
    def test_ensure_configured(self):
        with patch("sync.services.pro_transport_mapping.settings") as mock_settings:
            mock_settings.DATABASES = {"default": {}}
            with self.assertRaises(ImproperlyConfigured):
                mapping.ensure_pro_transport_configured()
