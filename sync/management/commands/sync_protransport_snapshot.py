from django.core.management.base import BaseCommand

from sync.services.pro_transport_import import sync_snapshot


class Command(BaseCommand):
    help = "Sync drivers and trucks snapshot from Pro Transport (placeholder)."

    def handle(self, *args, **options):
        self.stdout.write("Starting Pro Transport snapshot sync...")
        self.stdout.write(
            self.style.WARNING(
                "Placeholder only — implement pro_transport_import.sync_snapshot() "
                "when Pro Transport DB connection is configured."
            )
        )

        result = sync_snapshot()

        self.stdout.write(
            self.style.SUCCESS(
                f"Sync finished. Drivers: +{result.drivers_created} / ~{result.drivers_updated}, "
                f"Trucks: +{result.trucks_created} / ~{result.trucks_updated}, "
                f"Driver links: {result.assignments_linked}."
            )
        )
        self.stdout.write(
            "After real sync, open / to review the fleet and correct missing dates per truck."
        )
