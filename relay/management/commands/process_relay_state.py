from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date

from relay.services import relay_service


class Command(BaseCommand):
    help = (
        "Idempotent as-of-today relay processor: complete due ACTIVE assignments, "
        "activate due PLANNED assignments, sync truck/driver cache and status periods."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-of",
            dest="as_of",
            default=None,
            help="Process as of YYYY-MM-DD (default: today).",
        )

    def handle(self, *args, **options):
        as_of = None
        if options["as_of"]:
            as_of = parse_date(options["as_of"])
            if as_of is None:
                self.stderr.write(self.style.ERROR("Invalid --as-of date. Use YYYY-MM-DD."))
                return

        result = relay_service.process_relay_state(as_of_date=as_of)
        self.stdout.write(
            self.style.SUCCESS(
                f"process_relay_state complete — activated={result.activated}, "
                f"completed={result.completed}, yarded={result.yarded}, "
                f"demoted={result.demoted}, home_time_cleared={result.home_time_cleared}"
            )
        )
