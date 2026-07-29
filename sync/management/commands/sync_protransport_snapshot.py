from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from sync.services.master_sync import sync_master


class Command(BaseCommand):
    help = (
        "Deprecated alias for sync_protransport_master. "
        "Use sync_protransport_master going forward."
    )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "sync_protransport_snapshot is deprecated; "
                "delegating to sync_protransport_master."
            )
        )
        try:
            result = sync_master()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync finished. Drivers: +{result.drivers_created} / "
                f"~{result.drivers_updated}, "
                f"Trucks: +{result.trucks_created} / ~{result.trucks_updated}."
            )
        )
        if result.errors:
            raise CommandError("Pro Transport sync completed with row errors.")
