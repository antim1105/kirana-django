"""Delete every record, keeping shop settings. Asks first."""

from django.core.management.base import BaseCommand

from manager.services import demo_data


class Command(BaseCommand):
    help = "Delete all products, wholesalers, rates and purchases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options):
        if not options["no_input"]:
            answer = input("Delete every record? Type 'yes' to go ahead: ")
            if answer.strip().lower() != "yes":
                self.stdout.write("Nothing deleted.")
                return

        counts = demo_data.clear_all_data()
        total = sum(counts.values())
        self.stdout.write(self.style.SUCCESS(f"Deleted {total} records."))
