from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import BaseCommand, CommandError

from sync.services.bootstrap import bootstrap_assignments
from sync.services.master_sync import sync_master


class Command(BaseCommand):
    help = (
        "Bootstrap ACTIVE RelayAssignments from Pro Transport truck↔driver links. "
        "Requires --confirm (or --dry-run). Does not overwrite existing planning."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required to write assignments (unless --dry-run).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report actions without creating assignments.",
        )
        parser.add_argument(
            "--skip-master-sync",
            action="store_true",
            help="Do not run master sync before bootstrap.",
        )
        parser.add_argument(
            "--default-start-date",
            type=str,
            default="",
            help="Cutover/estimated start date YYYY-MM-DD (required if not in settings).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        confirm = options["confirm"]
        if not dry_run and not confirm:
            raise CommandError(
                "Refusing to run without --confirm. "
                "Use --dry-run to preview, or --confirm to create assignments."
            )

        default_start = options["default_start_date"] or None
        run_master = not options["skip_master_sync"]

        self.stdout.write("Starting Pro Transport assignment bootstrap...")
        try:
            if run_master and not dry_run:
                self.stdout.write("Running master sync first...")
                sync_master()
            result = bootstrap_assignments(
                confirm=confirm or dry_run,
                dry_run=dry_run,
                run_master_sync=False,  # already handled above
                default_start_date=default_start,
            )
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for line in result.as_lines():
            self.stdout.write(line)
        if result.warnings:
            self.stdout.write(self.style.WARNING(f"{len(result.warnings)} warning(s):"))
            for message in result.warnings[:80]:
                self.stdout.write(f"  ! {message}")
        if result.errors:
            self.stdout.write(self.style.WARNING(f"{len(result.errors)} detail(s):"))
            for message in result.errors[:80]:
                self.stdout.write(f"  - {message}")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run only — no assignments written."))
        else:
            self.stdout.write(self.style.SUCCESS("Bootstrap finished."))
