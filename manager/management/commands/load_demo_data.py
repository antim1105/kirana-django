"""Fill the database with the sample rates, for trying the app out."""

from django.core.management.base import BaseCommand

from manager.services import demo_data


class Command(BaseCommand):
    help = "Load sample products, wholesalers, rates and purchases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            action="store_true",
            help="Add the sample data without clearing what is already there.",
        )

    def handle(self, *args, **options):
        counts = demo_data.load_demo_data(reset=not options["keep"])
        self.stdout.write(
            self.style.SUCCESS(
                "Loaded {products} products, {wholesalers} wholesalers, "
                "{rates} rates and {purchases} purchases.".format(**counts)
            )
        )
