from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from drivers.models import Driver, DriverStatus, EmploymentStatus
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
        # Relative to "today" so tests stay valid as the calendar moves.
        # Default cycle is 4 weeks ending today (half-open end = today).
        self.today = timezone.localdate()
        self.start = self.today - timedelta(weeks=4)
        self.end = relay_service.calculate_expected_end_date(self.start)

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
        today = timezone.localdate()
        start = today - timedelta(days=20)
        end = today  # returns home today → still on home time
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=end,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(assignment, actual_end_date=end)

        assignment.refresh_from_db()
        self.driver_a.refresh_from_db()

        self.assertEqual(assignment.status, AssignmentStatus.COMPLETED)
        self.assertEqual(self.driver_a.status, DriverStatus.HOME_TIME)

        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.start_date, end)
        self.assertEqual(home.end_date, end + timedelta(days=7))

        otr = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.OTR,
        )
        self.assertEqual(otr.end_date, end)

    def test_complete_with_custom_home_time_days(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(
            assignment,
            actual_end_date=self.end,
            home_time_days=5,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.home_time_days, 5)
        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.end_date, self.end + timedelta(days=5))

    def test_active_assignment_records_planned_home_time_in_history(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
            home_time_days=5,
        )
        self.driver_a.refresh_from_db()

        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.start_date, assignment.expected_end_date)
        self.assertEqual(home.end_date, assignment.expected_end_date + timedelta(days=5))
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

    def test_planned_home_time_status_changes_only_on_return_day(self):
        today = timezone.localdate()
        start = today - timedelta(days=10)
        return_home = today + timedelta(days=3)
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=return_home,
            status=AssignmentStatus.ACTIVE,
            home_time_days=4,
        )
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

        relay_service.process_relay_state(as_of_date=today)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

        relay_service.process_relay_state(as_of_date=return_home)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.HOME_TIME)

    def test_update_home_time_syncs_future_home_period(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        new_home = self.start + timedelta(weeks=3)
        relay_service.update_assignment_home_time(assignment, new_home)
        relay_service.update_assignment_home_time_days(assignment, 10)

        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.start_date, new_home)
        self.assertEqual(home.end_date, new_home + timedelta(days=10))
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

    def test_moving_start_to_future_demotes_active_to_planned(self):
        today = timezone.localdate()
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=today - timedelta(days=5),
            expected_end_date=today + timedelta(days=20),
            status=AssignmentStatus.ACTIVE,
        )
        self.truck.refresh_from_db()
        self.assertEqual(self.truck.current_driver_id, self.driver_a.id)

        tomorrow = today + timedelta(days=1)
        new_end = tomorrow + timedelta(days=20)
        updated = relay_service.update_assignment_home_time(
            assignment,
            new_end,
            start_date=tomorrow,
        )
        self.assertEqual(updated.status, AssignmentStatus.PLANNED)
        self.assertIsNone(relay_service.get_current_assignment_for_truck(self.truck))
        next_planned = relay_service.get_next_assignment_for_truck(self.truck)
        self.assertIsNotNone(next_planned)
        self.assertEqual(next_planned.pk, updated.pk)
        self.truck.refresh_from_db()
        self.assertIsNone(self.truck.current_driver)
        self.assertFalse(
            DriverStatusPeriod.objects.filter(
                assignment=updated,
                status=DriverPeriodStatus.OTR,
            ).exists()
        )

    def test_process_demotes_stale_future_active(self):
        today = timezone.localdate()
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=today - timedelta(days=2),
            expected_end_date=today + timedelta(days=20),
            status=AssignmentStatus.ACTIVE,
        )
        # Simulate stuck state: ACTIVE with future start.
        assignment.start_date = today + timedelta(days=1)
        assignment.save(update_fields=["start_date", "updated_at"])

        result = relay_service.process_relay_state(as_of_date=today)
        self.assertEqual(result.demoted, 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.PLANNED)

    def test_complete_rejects_future_end_date(self):
        today = timezone.localdate()
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=today - timedelta(days=10),
            expected_end_date=today + timedelta(days=10),
            status=AssignmentStatus.ACTIVE,
        )
        with self.assertRaises(ValidationError):
            relay_service.complete_assignment(
                assignment,
                actual_end_date=today + timedelta(days=1),
            )

    def test_process_catches_up_activate_then_complete(self):
        today = timezone.localdate()
        # Planned that already started and already ended — one process pass should settle.
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=today - timedelta(days=20),
            expected_end_date=today - timedelta(days=5),
            status=AssignmentStatus.PLANNED,
            home_time_days=3,
        )
        result = relay_service.process_relay_state(as_of_date=today)
        self.assertGreaterEqual(result.activated, 1)
        self.assertGreaterEqual(result.completed, 1)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)
        self.truck.refresh_from_db()
        self.assertIsNone(self.truck.current_driver)

    def test_update_open_home_time_period_duration(self):
        today = timezone.localdate()
        start = today - timedelta(days=20)
        end = today - timedelta(days=2)
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=end,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(
            assignment,
            actual_end_date=end,
            home_time_days=14,
        )
        period = relay_service.get_open_home_time_period(self.driver_a)
        self.assertIsNotNone(period)
        new_end = today + timedelta(days=5)
        updated = relay_service.update_driver_home_time_period(period, new_end)
        self.assertEqual(updated.end_date, new_end)
        assignment.refresh_from_db()
        self.assertEqual(assignment.home_time_days, (new_end - end).days)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.HOME_TIME)

        # Ending home time today/earlier clears driver to Active.
        relay_service.update_driver_home_time_period(updated, today)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

    def test_backdated_complete_sets_active_when_home_already_ended(self):
        today = timezone.localdate()
        start = today - timedelta(days=30)
        home_start = today - timedelta(days=12)
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=home_start,
            status=AssignmentStatus.ACTIVE,
            home_time_days=7,
        )
        relay_service.complete_assignment(
            assignment,
            actual_end_date=home_start,
            home_time_days=7,
        )
        self.driver_a.refresh_from_db()
        # Home ended 5 days ago — available, not stuck on Home Time.
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)
        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.end_date, home_start + timedelta(days=7))
        self.assertIsNone(relay_service.get_open_home_time_period(self.driver_a))

    def test_process_relay_state_clears_expired_home_time_status(self):
        today = timezone.localdate()
        start = today - timedelta(days=30)
        home_start = today - timedelta(days=12)
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=home_start,
            status=AssignmentStatus.ACTIVE,
            home_time_days=7,
        )
        # Simulate stuck status after home window already ended.
        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        home.start_date = home_start
        home.end_date = home_start + timedelta(days=7)
        home.save(update_fields=["start_date", "end_date", "updated_at"])
        assignment.status = AssignmentStatus.COMPLETED
        assignment.actual_end_date = home_start
        assignment.save(update_fields=["status", "actual_end_date", "updated_at"])
        self.truck.current_driver = None
        self.truck.status = TruckStatus.YARD
        self.truck.save(update_fields=["current_driver", "status", "updated_at"])
        self.driver_a.status = DriverStatus.HOME_TIME
        self.driver_a.save(update_fields=["status", "updated_at"])

        result = relay_service.process_relay_state(as_of_date=today)
        self.assertEqual(result.home_time_cleared, 1)
        self.driver_a.refresh_from_db()
        self.assertEqual(self.driver_a.status, DriverStatus.ACTIVE)

    def test_second_driver_can_take_truck_immediately_and_stays_otr(self):
        today = timezone.localdate()
        start = today - timedelta(days=20)
        end = today
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=start,
            expected_end_date=end,
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
        self.today = timezone.localdate()
        # Monday of the current week — stable week-circle anchors.
        self.start = self.today - timedelta(days=self.today.weekday())
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
        # Mid-week handoff so both drivers occupy the same ISO week.
        handoff = self.start + timedelta(weeks=2, days=3)
        first = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            expected_end_date=handoff,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.create_assignment(
            driver=self.driver_b,
            truck=self.truck,
            start_date=handoff,
            status=AssignmentStatus.PLANNED,
        )
        headers, circles = self._circles_for(self.truck, start_date=self.start)
        handoff_header = next(
            h for h in headers if h.week_start <= handoff < h.week_end
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

    def test_day_status_occupied_and_available(self):
        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        row = relay_service.get_truck_board_row(self.truck)
        occupied = relay_service.get_truck_day_status(row, self.start)
        self.assertEqual(occupied["status"], "otr")
        self.assertEqual(occupied["driver"], "John A")

        free_day = self.end + timedelta(days=1)
        free = relay_service.get_truck_day_status(row, free_day)
        self.assertEqual(free["status"], "review")

        empty_truck = Truck.objects.create(unit_number="T-FREE", status=TruckStatus.AVAILABLE)
        empty_row = relay_service.get_truck_board_row(empty_truck)
        available = relay_service.get_truck_day_status(empty_row, self.today)
        self.assertEqual(available["status"], "home")

    def test_driver_gantt_shows_truck_bars_and_status(self):
        from relay.presentation.driver_gantt import build_driver_gantt

        relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        history = relay_service.get_driver_history(self.driver_a)
        headers = relay_service.get_board_week_headers(8, start_date=self.start)
        gantt = build_driver_gantt(
            history["assignments"],
            history["status_periods"],
            headers,
        )
        self.assertTrue(gantt.has_content)
        self.assertTrue(gantt.bars)
        self.assertEqual(gantt.bars[0].truck_label, "T-300")
        self.assertTrue(gantt.day_headers)
        self.assertTrue(any(seg.kind == "otr" for seg in gantt.status_segments))

    def test_driver_timeline_otr_and_home(self):
        assignment = relay_service.create_assignment(
            driver=self.driver_a,
            truck=self.truck,
            start_date=self.start,
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(
            assignment,
            actual_end_date=self.end,
            as_of_date=self.end,
        )
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

        assignment = relay_service.plan_next_assignment(
            self.truck,
            self.driver_a,
            self.start,
            notes="plan",
        )
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

    def test_edit_planned_without_current_assignment(self):
        today = timezone.localdate()
        planned = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=today + timedelta(days=1),
            expected_end_date=today + timedelta(days=22),
            status=AssignmentStatus.PLANNED,
        )
        self.client.force_login(self.it_user)

        # Modal must render even though there is no current assignment.
        response = self.client.get(f"/trucks/{self.truck.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Planned")
        self.assertContains(response, "modal-plan-next")

        # Editing the planned assignment must go through.
        new_start = today + timedelta(days=3)
        new_end = new_start + timedelta(weeks=4)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/plan-next/",
            {
                "plan-next_driver": self.driver.pk,
                "plan-start_date": new_start.isoformat(),
                "plan-home_time_date": new_end.isoformat(),
                "plan-notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        planned.refresh_from_db()
        self.assertEqual(planned.start_date, new_start)
        self.assertEqual(planned.expected_end_date, new_end)
        self.assertEqual(planned.status, AssignmentStatus.PLANNED)

    def test_start_assignment_creates_active(self):
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/start/",
            {
                "start-driver": self.driver.pk,
                "start-start_date": timezone.localdate().isoformat(),
                "start-home_time_date": (
                    timezone.localdate() + timedelta(weeks=4)
                ).isoformat(),
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
                "plan-home_time_date": (start + timedelta(weeks=4)).isoformat(),
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
                "start-home_time_date": (
                    timezone.localdate() + timedelta(weeks=4)
                ).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(RelayAssignment.objects.filter(truck=self.truck).exists())

    def test_update_home_time_updates_expected_end(self):
        assignment = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate(),
            status=AssignmentStatus.ACTIVE,
        )
        new_start = assignment.start_date - timedelta(days=3)
        new_home = new_start + timedelta(weeks=3, days=5)
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/home-time/",
            {
                "home-start_date": new_start.isoformat(),
                "home-home_time_date": new_home.isoformat(),
                "home-back_to_work_date": (new_home + timedelta(days=5)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.start_date, new_start)
        self.assertEqual(assignment.expected_end_date, new_home)
        self.assertEqual(
            relay_service.format_cycle_duration(assignment.start_date, new_home),
            "3 weeks 5 days",
        )
        row = relay_service.get_truck_board_row(self.truck)
        self.assertEqual(row.expected_home_date, new_home)
        otr = assignment.driver_status_periods.filter(status="otr").first()
        self.assertIsNotNone(otr)
        self.assertEqual(otr.start_date, new_start)

    def test_driver_update_home_time_frees_truck_on_timeline(self):
        assignment = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate(),
            status=AssignmentStatus.ACTIVE,
        )
        new_home = assignment.start_date + timedelta(weeks=2)
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/drivers/{self.driver.pk}/assignments/home-time/",
            {
                "home-start_date": assignment.start_date.isoformat(),
                "home-home_time_date": new_home.isoformat(),
                "home-back_to_work_date": (new_home + timedelta(days=4)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.expected_end_date, new_home)
        row = relay_service.get_truck_board_row(self.truck)
        self.assertEqual(row.expected_home_date, new_home)
        self.assertEqual(row.current_driver.pk, self.driver.pk)
        period = assignment.driver_status_periods.filter(status="otr").first()
        self.assertIsNotNone(period)
        self.assertEqual(period.end_date, new_home)
        home = assignment.driver_status_periods.filter(status="home_time").first()
        self.assertIsNotNone(home)
        self.assertEqual(home.start_date, new_home)
        self.assertEqual(home.end_date, new_home + timedelta(days=4))
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.status, DriverStatus.ACTIVE)

    def test_complete_posts_custom_home_time_days(self):
        relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate() - timedelta(days=10),
            status=AssignmentStatus.ACTIVE,
        )
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/assignments/complete/",
            {
                "actual_end_date": timezone.localdate().isoformat(),
                "home_time_days": "5",
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment = RelayAssignment.objects.get(truck=self.truck)
        self.assertEqual(assignment.status, AssignmentStatus.COMPLETED)
        self.assertEqual(assignment.home_time_days, 5)
        home = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(home.end_date, timezone.localdate() + timedelta(days=5))

    def test_driver_edit_home_time_period(self):
        today = timezone.localdate()
        assignment = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=today - timedelta(days=20),
            expected_end_date=today - timedelta(days=1),
            status=AssignmentStatus.ACTIVE,
        )
        relay_service.complete_assignment(
            assignment,
            actual_end_date=today - timedelta(days=1),
            home_time_days=10,
        )
        new_end = today + timedelta(days=4)
        self.client.force_login(self.it_user)
        response = self.client.post(
            f"/drivers/{self.driver.pk}/home-time-period/",
            {"htperiod-end_date": new_end.isoformat()},
        )
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.home_time_days, 5)
        period = DriverStatusPeriod.objects.get(
            assignment=assignment,
            status=DriverPeriodStatus.HOME_TIME,
        )
        self.assertEqual(period.end_date, new_end)

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


class SpreadsheetImportTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.SimpleUploadedFile = SimpleUploadedFile
        it = Department.objects.create(name="IT", slug="it-import")
        recruiting = Department.objects.create(name="Recruiting", slug="recruiting-import")
        admin_role = Role.objects.create(
            name="Admin",
            slug="admin-import",
            department=it,
            access_level=AccessLevel.FULL,
        )
        read_role = Role.objects.create(
            name="Read Only",
            slug="read-import",
            department=recruiting,
            access_level=AccessLevel.READ,
        )
        self.it_user = User.objects.create_user(
            email="import-admin@example.com",
            password="pass12345",
            full_name="Import Admin",
            department=it,
            role=admin_role,
        )
        self.read_user = User.objects.create_user(
            email="import-reader@example.com",
            password="pass12345",
            full_name="Import Reader",
            department=recruiting,
            role=read_role,
        )

    def test_import_drivers_from_csv(self):
        from relay.services import spreadsheet_import

        csv_bytes = (
            b"first_name,last_name,phone,status,driver_type,hire_date\n"
            b"Jane,Doe,555-1111,Active,Company Driver,2024-02-01\n"
            b"Mike,Ross,,pending,owner_operator,\n"
        )
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("drivers.csv", csv_bytes)
        )
        result = spreadsheet_import.import_drivers(rows)
        self.assertEqual(result.created, 2)
        self.assertEqual(result.updated, 0)
        jane = Driver.objects.get(first_name="Jane", last_name="Doe")
        self.assertEqual(jane.status, DriverStatus.ACTIVE)
        self.assertEqual(jane.hire_date, date(2024, 2, 1))

    def test_import_drivers_updates_by_name(self):
        from relay.services import spreadsheet_import

        Driver.objects.create(first_name="Jane", last_name="Doe", phone="old")
        csv_bytes = b"first_name,last_name,phone\nJane,Doe,555-9999\n"
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("drivers.csv", csv_bytes)
        )
        result = spreadsheet_import.import_drivers(rows)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Driver.objects.get(first_name="Jane", last_name="Doe").phone, "555-9999")

    def test_import_drivers_matches_driver_id(self):
        from relay.services import spreadsheet_import

        Driver.objects.create(
            first_name="Old",
            last_name="Name",
            driver_id="DRV-42",
            phone="111",
        )
        csv_bytes = (
            b"driver_id,first_name,last_name,phone\n"
            b"DRV-42,New,Name,555\n"
        )
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("drivers.csv", csv_bytes)
        )
        result = spreadsheet_import.import_drivers(rows)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.created, 0)
        driver = Driver.objects.get(driver_id="DRV-42")
        self.assertEqual(driver.first_name, "New")
        self.assertEqual(driver.phone, "555")

    def test_import_drivers_links_driver_id_by_name(self):
        from relay.services import spreadsheet_import

        Driver.objects.create(first_name="Jane", last_name="Doe")
        csv_bytes = b"driver_id,first_name,last_name\nEXT-9,Jane,Doe\n"
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("drivers.csv", csv_bytes)
        )
        result = spreadsheet_import.import_drivers(rows)
        self.assertEqual(result.updated, 1)
        self.assertEqual(Driver.objects.get(first_name="Jane", last_name="Doe").driver_id, "EXT-9")

    def test_import_trucks_from_csv_upserts_unit(self):
        from relay.services import spreadsheet_import

        Truck.objects.create(unit_number="T-101", make="Old")
        csv_bytes = (
            b"unit_number,make,model,year,status\n"
            b"T-101,Freightliner,Cascadia,2022,available\n"
            b"T-202,Kenworth,T680,2021,yard\n"
        )
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("trucks.csv", csv_bytes)
        )
        result = spreadsheet_import.import_trucks(rows)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 1)
        truck = Truck.objects.get(unit_number="T-101")
        self.assertEqual(truck.make, "Freightliner")
        self.assertEqual(truck.year, 2022)
        self.assertEqual(Truck.objects.get(unit_number="T-202").status, TruckStatus.YARD)

    def test_import_trucks_does_not_set_current_driver_without_assignment(self):
        from relay.models import RelayAssignment
        from relay.services import spreadsheet_import

        Driver.objects.create(
            first_name="Link",
            last_name="Driver",
            driver_id="DRV-77",
        )
        csv_bytes = (
            b"unit_number,driver_id,make,status\n"
            b"T-300,DRV-77,Volvo,otr\n"
        )
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("trucks.csv", csv_bytes)
        )
        result = spreadsheet_import.import_trucks(rows, create_initial_assignments=False)
        self.assertEqual(result.created, 1)
        truck = Truck.objects.get(unit_number="T-300")
        self.assertIsNone(truck.current_driver_id)
        self.assertEqual(truck.status, TruckStatus.OTR)
        self.assertFalse(RelayAssignment.objects.filter(truck=truck).exists())

    def test_import_trucks_creates_initial_assignment(self):
        from datetime import timedelta

        from relay.models import AssignmentStatus, RelayAssignment
        from relay.services import spreadsheet_import

        driver = Driver.objects.create(
            first_name="On",
            last_name="Road",
            driver_id="DRV-88",
        )
        csv_bytes = b"unit_number,driver_id\nT-400,DRV-88\n"
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("trucks.csv", csv_bytes)
        )
        result = spreadsheet_import.import_trucks(rows, create_initial_assignments=True)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.assignments_created, 1)
        truck = Truck.objects.get(unit_number="T-400")
        assignment = RelayAssignment.objects.get(truck=truck)
        self.assertEqual(assignment.status, AssignmentStatus.ACTIVE)
        self.assertEqual(assignment.driver_id, driver.pk)
        today = timezone.localdate()
        self.assertEqual(assignment.start_date, today - timedelta(weeks=2))
        self.assertEqual(assignment.expected_end_date, today + timedelta(weeks=2))
        truck.refresh_from_db()
        self.assertEqual(truck.status, TruckStatus.OTR)
        self.assertEqual(truck.current_driver_id, driver.pk)

        # Re-import must not create a second assignment.
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("trucks.csv", csv_bytes)
        )
        again = spreadsheet_import.import_trucks(rows, create_initial_assignments=True)
        self.assertEqual(again.assignments_created, 0)
        self.assertEqual(RelayAssignment.objects.filter(truck=truck).count(), 1)

    def test_import_trucks_unknown_driver_id_skipped(self):
        from relay.services import spreadsheet_import

        csv_bytes = b"unit_number,driver_id\nT-404,MISSING\n"
        rows = spreadsheet_import.read_spreadsheet_rows(
            self.SimpleUploadedFile("trucks.csv", csv_bytes)
        )
        result = spreadsheet_import.import_trucks(rows)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, 1)
        self.assertFalse(Truck.objects.filter(unit_number="T-404").exists())

    def test_import_trucks_from_xlsx(self):
        from io import BytesIO

        from openpyxl import Workbook
        from relay.services import spreadsheet_import

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["unit_number", "make", "status"])
        sheet.append(["X-1", "Volvo", "Available"])
        buffer = BytesIO()
        workbook.save(buffer)
        uploaded = self.SimpleUploadedFile(
            "trucks.xlsx",
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        result = spreadsheet_import.import_trucks(spreadsheet_import.read_spreadsheet_rows(uploaded))
        self.assertEqual(result.created, 1)
        self.assertTrue(Truck.objects.filter(unit_number="X-1").exists())

    def test_drivers_import_view_requires_full_access(self):
        csv_bytes = b"first_name,last_name\nA,B\n"
        uploaded = self.SimpleUploadedFile("drivers.csv", csv_bytes, content_type="text/csv")
        self.client.force_login(self.read_user)
        response = self.client.post("/drivers/import/", {"file": uploaded})
        self.assertEqual(response.status_code, 403)

    def test_drivers_import_view_creates_records(self):
        csv_bytes = b"first_name,last_name\nImport,Person\n"
        uploaded = self.SimpleUploadedFile("drivers.csv", csv_bytes, content_type="text/csv")
        self.client.force_login(self.it_user)
        response = self.client.post("/drivers/import/", {"file": uploaded})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Driver.objects.filter(first_name="Import", last_name="Person").exists())

    def test_list_pages_show_import_button(self):
        self.client.force_login(self.it_user)
        drivers = self.client.get("/drivers/")
        trucks = self.client.get("/trucks/")
        self.assertContains(drivers, "Import")
        self.assertContains(drivers, 'data-modal-open="import-drivers"')
        self.assertContains(trucks, 'data-modal-open="import-trucks"')


class ListFilterTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role

        it = Department.objects.create(name="IT", slug="it-filters")
        role = Role.objects.create(
            name="Admin",
            slug="admin-filters",
            department=it,
            access_level=AccessLevel.FULL,
        )
        self.user = User.objects.create_user(
            email="filters@example.com",
            password="pass12345",
            full_name="Filter Admin",
            department=it,
            role=role,
        )
        self.active = Driver.objects.create(
            first_name="Anna",
            last_name="Active",
            driver_id="D-1",
            status=DriverStatus.ACTIVE,
            phone="555-1000",
        )
        self.pending = Driver.objects.create(
            first_name="Paul",
            last_name="Pending",
            driver_id="D-2",
            status=DriverStatus.PENDING,
        )
        self.terminated = Driver.objects.create(
            first_name="Tom",
            last_name="Gone",
            status=DriverStatus.TERMINATED,
        )
        self.ex_employee = Driver.objects.create(
            first_name="Ex",
            last_name="Employee",
            driver_id="D-EX",
            status=DriverStatus.PENDING,
            employment_status=EmploymentStatus.TERMINATED,
        )
        self.truck_otr = Truck.objects.create(
            unit_number="U-100",
            status=TruckStatus.OTR,
            make="Volvo",
            current_driver=self.active,
        )
        self.truck_free = Truck.objects.create(
            unit_number="U-200",
            status=TruckStatus.AVAILABLE,
            make="Kenworth",
        )

    def test_drivers_default_hides_terminated(self):
        self.client.force_login(self.user)
        response = self.client.get("/drivers/")
        self.assertContains(response, "Anna Active")
        self.assertNotContains(response, "Tom Gone")
        self.assertNotContains(response, "Ex Employee")

    def test_drivers_employment_all_shows_ex_employee(self):
        self.client.force_login(self.user)
        response = self.client.get("/drivers/", {"employment": "all", "status": "all"})
        self.assertContains(response, "Ex Employee")
        self.assertContains(response, "Tom Gone")

    def test_drivers_search_and_status_filter(self):
        self.client.force_login(self.user)
        by_id = self.client.get("/drivers/", {"q": "D-1"})
        self.assertContains(by_id, "Anna Active")
        self.assertNotContains(by_id, "Paul Pending")

        by_status = self.client.get("/drivers/", {"status": "pending"})
        self.assertContains(by_status, "Paul Pending")
        self.assertNotContains(by_status, "Anna Active")

        all_statuses = self.client.get("/drivers/", {"status": "all"})
        self.assertContains(all_statuses, "Tom Gone")

    def test_trucks_search_and_assigned_filter(self):
        self.client.force_login(self.user)
        response = self.client.get("/trucks/", {"q": "Volvo"})
        self.assertContains(response, "U-100")
        self.assertNotContains(response, "U-200")

        unassigned = self.client.get("/trucks/", {"assigned": "no"})
        self.assertContains(unassigned, "U-200")
        self.assertNotContains(unassigned, "U-100")

        by_driver = self.client.get("/trucks/", {"q": "Anna"})
        self.assertContains(by_driver, "U-100")

    def test_drivers_pagination(self):
        for i in range(25):
            Driver.objects.create(first_name=f"P{i}", last_name="Pager")
        self.client.force_login(self.user)
        page1 = self.client.get("/drivers/", {"per_page": 20})
        self.assertEqual(page1.status_code, 200)
        self.assertContains(page1, "Show")
        self.assertContains(page1, "per page")
        self.assertContains(page1, "Next →")
        page2 = self.client.get("/drivers/", {"per_page": 20, "page": 2})
        self.assertEqual(page2.status_code, 200)
        self.assertContains(page2, "← Prev")

    def test_fleet_board_pagination(self):
        for i in range(25):
            Truck.objects.create(unit_number=f"P-{i:03d}")
        self.client.force_login(self.user)
        page1 = self.client.get("/", {"per_page": 20})
        self.assertEqual(page1.status_code, 200)
        self.assertContains(page1, "Show")
        self.assertContains(page1, "per page")
        self.assertContains(page1, "Next →")
        self.assertContains(page1, "1–20")
        self.assertEqual(len(page1.context["fleet_items"]), 20)
        page2 = self.client.get("/", {"per_page": 20, "page": 2})
        self.assertEqual(page2.status_code, 200)
        self.assertContains(page2, "← Prev")
        self.assertGreaterEqual(len(page2.context["fleet_items"]), 1)


class EditListModalTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role

        it = Department.objects.create(name="IT", slug="it-edit")
        role = Role.objects.create(
            name="Admin",
            slug="admin-edit",
            department=it,
            access_level=AccessLevel.FULL,
        )
        self.user = User.objects.create_user(
            email="edit@example.com",
            password="pass12345",
            full_name="Edit Admin",
            department=it,
            role=role,
        )
        self.driver = Driver.objects.create(
            first_name="Edit",
            last_name="Me",
            driver_id="E-1",
            status=DriverStatus.ACTIVE,
        )
        self.truck = Truck.objects.create(unit_number="E-100", status=TruckStatus.AVAILABLE)

    def test_list_shows_edit_buttons(self):
        self.client.force_login(self.user)
        drivers = self.client.get("/drivers/")
        trucks = self.client.get("/trucks/")
        self.assertContains(drivers, 'data-modal-open="driver-edit"')
        self.assertContains(drivers, f"/drivers/{self.driver.pk}/edit/")
        self.assertContains(trucks, 'data-modal-open="truck-edit"')
        self.assertContains(trucks, f"/trucks/{self.truck.pk}/edit/")

    def test_driver_edit_updates_record(self):
        self.client.force_login(self.user)
        response = self.client.post(
            f"/drivers/{self.driver.pk}/edit/",
            {
                "edit_driver-driver_id": "E-1",
                "edit_driver-first_name": "Edited",
                "edit_driver-last_name": "Driver",
                "edit_driver-phone": "",
                "edit_driver-email": "",
                "edit_driver-status": DriverStatus.HOME_TIME,
                "edit_driver-driver_type": "company_driver",
                "edit_driver-hire_date": "",
                "edit_driver-notes": "updated",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.first_name, "Edited")
        self.assertEqual(self.driver.status, DriverStatus.HOME_TIME)

    def test_truck_edit_does_not_write_current_driver_cache(self):
        self.client.force_login(self.user)
        response = self.client.post(
            f"/trucks/{self.truck.pk}/edit/",
            {
                "edit_truck-unit_number": "E-100",
                "edit_truck-driver_id": "E-1",
                "edit_truck-status": TruckStatus.OTR,
                "edit_truck-make": "Volvo",
                "edit_truck-model": "",
                "edit_truck-year": "",
                "edit_truck-vin": "",
                "edit_truck-notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.truck.refresh_from_db()
        self.assertIsNone(self.truck.current_driver_id)
        self.assertEqual(self.truck.status, TruckStatus.OTR)
        self.assertEqual(self.truck.make, "Volvo")


class DeleteRecordPermissionTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role

        it = Department.objects.create(name="IT", slug="it-delete")
        recruiting = Department.objects.create(name="Recruiting", slug="recruiting-delete")
        admin_role = Role.objects.create(
            name="Admin",
            slug="admin-delete",
            department=it,
            access_level=AccessLevel.FULL,
            can_create=True,
            can_edit=True,
            can_delete=True,
        )
        no_delete_role = Role.objects.create(
            name="Editor",
            slug="editor-delete",
            department=recruiting,
            access_level=AccessLevel.READ,
            can_create=True,
            can_edit=True,
            can_delete=False,
        )
        self.admin = User.objects.create_user(
            email="delete-admin@example.com",
            password="pass12345",
            full_name="Delete Admin",
            department=it,
            role=admin_role,
        )
        self.editor = User.objects.create_user(
            email="delete-editor@example.com",
            password="pass12345",
            full_name="No Delete",
            department=recruiting,
            role=no_delete_role,
        )
        self.driver = Driver.objects.create(first_name="Del", last_name="Driver", driver_id="DEL-1")
        self.truck = Truck.objects.create(unit_number="DEL-100")

    def test_list_shows_delete_only_with_capability(self):
        self.client.force_login(self.admin)
        response = self.client.get("/drivers/")
        self.assertContains(response, f"/drivers/{self.driver.pk}/delete/")
        self.assertContains(response, 'data-modal-open="delete-confirm"')

        self.client.force_login(self.editor)
        response = self.client.get("/drivers/")
        self.assertNotContains(response, f"/drivers/{self.driver.pk}/delete/")
        self.assertContains(response, 'data-modal-open="driver-edit"')

    def test_delete_driver_and_truck(self):
        self.client.force_login(self.admin)
        response = self.client.post(f"/drivers/{self.driver.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Driver.objects.filter(pk=self.driver.pk).exists())

        response = self.client.post(f"/trucks/{self.truck.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Truck.objects.filter(pk=self.truck.pk).exists())

    def test_delete_blocked_without_capability(self):
        self.client.force_login(self.editor)
        response = self.client.post(f"/trucks/{self.truck.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Truck.objects.filter(pk=self.truck.pk).exists())

    def test_delete_blocked_when_active_assignment_exists(self):
        from relay.services import relay_service

        relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=timezone.localdate(),
            status=AssignmentStatus.ACTIVE,
        )
        self.client.force_login(self.admin)
        response = self.client.post(f"/trucks/{self.truck.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Truck.objects.filter(pk=self.truck.pk).exists())


class GetViewsDoNotMutateTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from departments.models import AccessLevel, Department, Role

        it = Department.objects.create(name="IT", slug="it-get")
        role = Role.objects.create(
            name="Admin",
            slug="admin-get",
            department=it,
            access_level=AccessLevel.FULL,
        )
        self.user = User.objects.create_user(
            email="get@example.com",
            password="pass12345",
            full_name="Getter",
            department=it,
            role=role,
        )
        self.driver = Driver.objects.create(first_name="G", last_name="Driver")
        self.truck = Truck.objects.create(unit_number="T-GET")
        today = timezone.localdate()
        # PLANNED that is due — process_relay_state would activate it.
        self.planned = relay_service.create_assignment(
            driver=self.driver,
            truck=self.truck,
            start_date=today - timedelta(days=1),
            expected_end_date=today + timedelta(weeks=4),
            status=AssignmentStatus.PLANNED,
        )

    def test_board_get_does_not_activate_due_planned(self):
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.planned.refresh_from_db()
        self.assertEqual(self.planned.status, AssignmentStatus.PLANNED)

    def test_truck_detail_get_does_not_activate_due_planned(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/trucks/{self.truck.pk}/")
        self.assertEqual(response.status_code, 200)
        self.planned.refresh_from_db()
        self.assertEqual(self.planned.status, AssignmentStatus.PLANNED)


class ActivateConcurrencyGuardTests(TestCase):
    def test_second_activate_on_same_truck_raises(self):
        driver_a = Driver.objects.create(first_name="A", last_name="One")
        driver_b = Driver.objects.create(first_name="B", last_name="Two")
        truck = Truck.objects.create(unit_number="T-LOCK")
        today = timezone.localdate()
        first = relay_service.create_assignment(
            driver=driver_a,
            truck=truck,
            start_date=today,
            status=AssignmentStatus.ACTIVE,
        )
        second = relay_service.create_assignment(
            driver=driver_b,
            truck=truck,
            start_date=today + timedelta(weeks=5),
            status=AssignmentStatus.PLANNED,
        )
        # Bypass model.clean overlap check to simulate a racey activate attempt.
        RelayAssignment.objects.filter(pk=second.pk).update(
            start_date=today,
            expected_end_date=today + timedelta(weeks=4),
        )
        second.refresh_from_db()
        with self.assertRaises(ValidationError):
            relay_service.activate_assignment(second)
        first.refresh_from_db()
        self.assertEqual(first.status, AssignmentStatus.ACTIVE)
        self.assertEqual(
            RelayAssignment.objects.filter(
                truck=truck, status=AssignmentStatus.ACTIVE
            ).count(),
            1,
        )
