from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from drivers.models import Driver, DriverStatus
from relay.models import (
    AssignmentStatus,
    DriverPeriodStatus,
    DriverStatusPeriod,
    RelayAssignment,
)
from relay.services import relay_service
from trucks.models import Truck, TruckStatus


class AssignmentLifecycleTests(TestCase):
    def setUp(self):
        self.driver_a = Driver.objects.create(
            first_name="Alice",
            last_name="Driver",
            status=DriverStatus.PENDING,
        )
        self.driver_b = Driver.objects.create(
            first_name="Bob",
            last_name="Driver",
            status=DriverStatus.PENDING,
        )
        self.truck = Truck.objects.create(
            unit_number="T-100",
            status=TruckStatus.AVAILABLE,
        )
        self.start = date(2026, 1, 5)
        self.end = relay_service.calculate_expected_end_date(self.start)  # +4 weeks

    def test_truck_overlap_rejected(self):
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError) as ctx:
            relay_service.create_assignment(
                driver=self.driver_b,
                truck=self.truck,
                start_date=self.start + timedelta(days=7),
                status=AssignmentStatus.PLANNED,
            )
        self.assertIn("truck", ctx.exception.message_dict)

    def test_driver_overlap_rejected(self):
        truck_2 = Truck.objects.create(unit_number="T-200")
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError) as ctx:
            relay_service.create_assignment(
                driver=self.driver_a,
                truck=truck_2,
                start_date=self.start + timedelta(days=3),
                status=AssignmentStatus.PLANNED,
            )
        self.assertIn("driver", ctx.exception.message_dict)

    def test_adjacent_assignments_allowed_same_day_handoff(self):
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        second = relay_service.create_assignment(
            driver=self.driver_b,
            truck=self.truck,
            start_date=first.expected_end_date,
            status=AssignmentStatus.PLANNED,
        )
        self.assertEqual(second.status, AssignmentStatus.PLANNED)
        self.assertEqual(second.start_date, first.expected_end_date)

    def test_complete_creates_home_time_period(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(assignment, actual_end_date=self.end)

        assignment.refresh_from_db()
        self.driver_a.refresh_from_db()

        self.assertEqual(assignment.status, AssignmentStatus.COMPLETED)
        self.assertEqual(self.driver_a.status, DriverStatus.HOME_TIME)

        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.start_date, self.end)
        self.assertEqual(home.end_date, self.end + timedelta(weeks=1))

        otr = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.OTR,
        )
        self.assertEqual(otr.end_date, self.end)

    def test_second_driver_can_take_truck_immediately_and_stays_otr(self):
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        second = relay_service.assign_next_driver(
            previous_assignment=first,
            next_driver=self.driver_b,
            start_date=first.expected_end_date,
            activate=False,
        )

        relay_service.complete_assignment(first, actual_end_date=first.expected_end_date)

        first.refresh_from_db()
        second.refresh_from_db()
        self.truck.refresh_from_db()
        self.driver_a.refresh_from_db()
        self.driver_b.refresh_from_db()

        self.assertEqual(first.status, AssignmentStatus.COMPLETED)
        self.assertEqual(second.status, AssignmentStatus.ACTIVE)
        self.assertEqual(self.truck.current_driver_id, self.driver_b.id)
        self.assertEqual(self.truck.status, TruckStatus.OTR)
        self.assertEqual(self.driver_a.status, DriverStatus.HOME_TIME)
        self.assertEqual(self.driver_b.status, DriverStatus.ACTIVE)

    def test_without_next_driver_truck_goes_to_yard(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(assignment, actual_end_date=self.end)

        self.truck.refresh_from_db()
        self.assertIsNone(self.truck.current_driver)
        self.assertEqual(self.truck.status, TruckStatus.YARD)

    def test_current_driver_cache_matches_active_assignment(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        self.truck.refresh_from_db()
        current = relay_service.get_current_assignment_for_truck(
            self.truck,
            as_of_date=self.start + timedelta(days=1),
        )
        self.assertEqual(current.pk, assignment.pk)
        self.assertEqual(self.truck.current_driver_id, self.driver_a.id)
        self.assertEqual(self.truck.status, TruckStatus.OTR)

    def test_completed_period_still_blocks_overlap(self):
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(first, actual_end_date=self.end)

        with self.assertRaises(ValidationError):
            relay_service.create_assignment(
                driver=self.driver_b,
                truck=self.truck,
                start_date=self.start + timedelta(days=2),
                status=AssignmentStatus.PLANNED,
            )


class TimelinePhaseTwoTests(TestCase):
    def setUp(self):
        self.driver_a = Driver.objects.create(first_name="John", last_name="A")
        self.driver_b = Driver.objects.create(first_name="Mike", last_name="B")
        self.truck = Truck.objects.create(unit_number="T-300", status=TruckStatus.AVAILABLE)
        self.start = date(2026, 7, 1)
        self.end = relay_service.calculate_expected_end_date(self.start)

    def _circles_for(self, truck, week_count=12, start_date=None):
        row = relay_service.get_truck_board_row(truck)
        headers = relay_service.get_board_week_headers(week_count, start_date=start_date or self.start)
        return headers, relay_service.get_truck_week_circles(row, headers, assignments=row.assignments)

    def test_occupied_week_is_red(self):
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        headers, circles = self._circles_for(self.truck, start_date=self.start)
        first = circles[0]
        self.assertEqual(first.status, "otr")
        self.assertIn("John", first.tooltip)

    def test_continuous_handoff_no_green_gap(self):
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.create_assignment(
            driver=self.driver_b,
            truck=self.truck,
            start_date=first.expected_end_date,
            status=AssignmentStatus.PLANNED,
        )
        headers, circles = self._circles_for(self.truck, week_count=12, start_date=self.start)
        statuses = [c.status for c in circles]
        # Across both assignments within horizon, no available gap week while covered
        covered = [c for c in circles if c.status == "otr"]
        self.assertGreaterEqual(len(covered), 7)
        self.assertNotIn("home", statuses[:8])

    def test_handoff_week_tooltip_lists_both_drivers(self):
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.create_assignment(
            driver=self.driver_b,
            truck=self.truck,
            start_date=first.expected_end_date,
            status=AssignmentStatus.PLANNED,
        )
        headers, circles = self._circles_for(self.truck, start_date=self.start)
        handoff_header = next(
            h for h in headers if h.week_start <= first.expected_end_date < h.week_end
        )
        circle = circles[headers.index(handoff_header)]
        self.assertEqual(circle.status, "otr")
        self.assertIn("John", circle.tooltip)
        self.assertIn("Mike", circle.tooltip)

    def test_uncovered_future_is_needs_planning(self):
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        headers, circles = self._circles_for(self.truck, week_count=16, start_date=self.start)
        after = [
            c
            for h, c in zip(headers, circles)
            if h.week_start >= self.end and h.week_end > timezone.localdate()
        ]
        self.assertTrue(after)
        self.assertEqual(after[0].status, "review")
        self.assertIn("Needs next driver", after[0].tooltip)

    def test_free_truck_is_available(self):
        headers, circles = self._circles_for(self.truck, start_date=timezone.localdate())
        self.assertEqual(circles[0].status, "home")

    def test_maintenance_is_gray(self):
        self.truck.status = TruckStatus.MAINTENANCE
        self.truck.save(update_fields=["status"])
        headers, circles = self._circles_for(self.truck, start_date=timezone.localdate())
        self.assertEqual(circles[0].status, "inactive")

    def test_driver_timeline_otr_and_home(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(assignment, actual_end_date=self.end)
        headers = relay_service.get_board_week_headers(12, start_date=self.start)
        circles = relay_service.get_driver_week_circles(self.driver_a, headers)
        statuses = {c.status for c in circles}
        self.assertIn("otr", statuses)
        self.assertIn("home", statuses)

    def test_iso_year_boundary_headers(self):
        # 2020-12-28 is Monday of ISO week 53, 2020
        start = date(2020, 12, 28)
        headers = relay_service.get_board_week_headers(3, start_date=start, today=start)
        self.assertEqual(headers[0].week_number, 53)
        self.assertEqual(headers[0].iso_year, 2020)
        self.assertEqual(headers[1].week_number, 1)
        self.assertEqual(headers[1].iso_year, 2021)
        self.assertEqual(headers[0].week_end, headers[1].week_start)

    def test_week_53_year_supported(self):
        start = date.fromisocalendar(2020, 53, 1)
        headers = relay_service.get_board_week_headers(1, start_date=start, today=start)
        self.assertEqual(headers[0].week_number, 53)
        self.assertEqual((headers[0].week_end - headers[0].week_start).days, 7)

    def test_quick_plan_creates_planned_assignment(self):
        from relay.models import RelayStatusOverride

        result = relay_service.save_relay_plan(
            self.truck,
            next_driver=self.driver_a,
            next_driver_start_date=self.start,
            notes="plan",
        )
        assignment = result["assignment"]
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.status, AssignmentStatus.PLANNED)
        self.assertEqual(assignment.driver_id, self.driver_a.id)
        self.assertFalse(RelayStatusOverride.objects.filter(truck=self.truck).exists())

    def test_process_relay_state_idempotent_and_activates(self):
        planned = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=timezone.localdate() - timedelta(days=1),
            status=AssignmentStatus.PLANNED,
        )
        first = relay_service.process_relay_state()
        self.assertGreaterEqual(first.activated, 1)
        planned.refresh_from_db()
        self.assertEqual(planned.status, AssignmentStatus.ACTIVE)
        otr_count = DriverStatusPeriod.objects.filter(
            assignment=planned,
            status=DriverPeriodStatus.OTR,
        ).count()
        second = relay_service.process_relay_state()
        self.assertEqual(second.activated, 0)
        self.assertEqual(
            DriverStatusPeriod.objects.filter(
                assignment=planned,
                status=DriverPeriodStatus.OTR,
            ).count(),
            otr_count,
        )

    def test_process_completes_due_active(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=timezone.localdate() - timedelta(weeks=5),
            status=AssignmentStatus.ACTIVE,
        )
        # Force expected end in the past
        assignment.expected_end_date = timezone.localdate() - timedelta(days=1)
        assignment.save()
        result = relay_service.process_relay_state()
        self.assertGreaterEqual(result.completed, 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.COMPLETED)
        self.truck.refresh_from_db()
        self.assertEqual(self.truck.status, TruckStatus.YARD)


class ManualWorkflowUITests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role
        from relay.models import RelayStatusOverride

        self.RelayStatusOverride = RelayStatusOverride
        it = Department.objects.create(name="IT", slug="it")
        recruiting = Department.objects.create(name="Recruiting", slug="recruiting")
        self.admin_role = Role.objects.create(
            name="Admin",
            slug="admin",
            department=it,
            access_level=AccessLevel.FULL,
        )
        self.read_role = Role.objects.create(
            name="Read Only",
            slug="read-only",
            department=recruiting,
            access_level=AccessLevel.READ,
        )
        self.it_user = User.objects.create_user(
            email="it@example.com",
            password="pass12345",
            full_name="IT Admin",
            department=it,
            role=self.admin_role,
        )
        self.recruiting_user = User.objects.create_user(
            email="recruiter@example.com",
            password="pass12345",
            full_name="Recruiter",
            department=recruiting,
            role=self.read_role,
        )
        self.driver = Driver.objects.create(first_name="Sam", last_name="Driver")
        self.truck = Truck.objects.create(unit_number="T-900", status=TruckStatus.AVAILABLE)

    def test_truck_without_active_shows_start_assignment(self):
        self.client.force_login(self.it_user)
        response = self.client.get(f"/trucks/{self.truck.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start Assignment")
        self.assertNotContains(response, "Status override")
        self.assertNotContains(response, "status_override")

    def test_start_assignment_creates_active(self):
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/start/",
            {
                "start-driver": self.driver.pk,
                "start-start_date": timezone.localdate().isoformat(),
                "start-cycle_weeks": 4,
                "start-notes": "go",
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment = RelayAssignment.objects.get(truck=self.truck)
        self.assertEqual(assignment.status, AssignmentStatus.ACTIVE)
        self.truck.refresh_from_db()
        self.assertEqual(self.truck.current_driver_id, self.driver.pk)
        self.assertEqual(self.truck.status, TruckStatus.OTR)
        self.assertFalse(self.RelayStatusOverride.objects.filter(truck=self.truck).exists())

    def test_plan_next_creates_planned_without_override(self):
        current = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate(),
            status=AssignmentStatus.ACTIVE,
        )
        next_driver = Driver.objects.create(first_name="Pat", last_name="Next")
        self.client.force_login(self.it_user)
        start = current.expected_end_date
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/plan-next/",
            {
                "plan-next_driver": next_driver.pk,
                "plan-start_date": start.isoformat(),
                "plan-cycle_weeks": 4,
                "plan-notes": "handoff",
            },
        )
        self.assertEqual(response.status_code, 302)
        planned = RelayAssignment.objects.get(truck=self.truck, status=AssignmentStatus.PLANNED)
        self.assertEqual(planned.driver_id, next_driver.pk)
        self.assertFalse(self.RelayStatusOverride.objects.filter(truck=self.truck).exists())

    def test_recruiting_cannot_start_assignment(self):
        self.client.force_login(self.recruiting_user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/start/",
            {
                "start-driver": self.driver.pk,
                "start-start_date": timezone.localdate().isoformat(),
                "start-cycle_weeks": 4,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(RelayAssignment.objects.filter(truck=self.truck).exists())

    def test_override_does_not_override_valid_assignment(self):
        from relay.models import RelayStatusOverride

        other = Driver.objects.create(first_name="Other", last_name="Drv")
        assignment = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate(),
            status=AssignmentStatus.ACTIVE,
        )
        RelayStatusOverride.objects.create(
            truck=self.truck,
            driver=other,
            cycle_start_date=timezone.localdate() - timedelta(days=100),
            notes="stale",
        )
        row = relay_service.get_truck_board_row(self.truck)
        self.assertEqual(row.current_driver.pk, self.driver.pk)
        self.assertEqual(row.cycle_start_date, assignment.start_date)
        self.assertNotEqual(row.notes, "stale")
