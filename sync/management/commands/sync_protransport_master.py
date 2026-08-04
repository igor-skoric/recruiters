from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from sync.services.master_sync import sync_master


class Command(BaseCommand):
    help = (
        "Periodic Pro Transport master sync (drivers/trucks only). "
        "New drivers: ACTIVE + is_active + company driver; "
        "owner operators are removed from the app; "
        "new trucks: only PT-active (see IMPORT_* allowlists). "
        "Does not touch RelayAssignment lifecycle except when removing "
        "out-of-scope drivers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report create/update/unchanged without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        label = "dry-run " if dry_run else ""
        self.stdout.write(f"Starting Pro Transport master sync ({label or 'write'})...")
        try:
            result = sync_master(dry_run=dry_run)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"dry_run={result.dry_run} "
                "Companies: "
                f"+{result.companies_created} ~{result.companies_updated} "
                f"={result.companies_unchanged} skip {result.companies_skipped}; "
                "Drivers: "
                f"+{result.drivers_created} ~{result.drivers_updated} "
                f"={result.drivers_unchanged} skip {result.drivers_skipped} "
                f"removed {result.drivers_removed}; "
                "Trucks: "
                f"+{result.trucks_created} ~{result.trucks_updated} "
                f"={result.trucks_unchanged} skip {result.trucks_skipped} "
                f"linked_by_unit {result.trucks_linked_by_unit}."
            )
        )
        if result.warnings:
            self.stdout.write(self.style.WARNING(f"{len(result.warnings)} warning(s):"))
            for message in result.warnings[:80]:
                self.stdout.write(f"  ! {message}")
        if result.errors:
            self.stdout.write(self.style.WARNING(f"{len(result.errors)} row error(s):"))
            for message in result.errors[:50]:
                self.stdout.write(f"  - {message}")
            if len(result.errors) > 50:
                self.stdout.write(f"  ... and {len(result.errors) - 50} more")
            raise CommandError("Pro Transport master sync completed with row errors.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run only — no database writes."))
