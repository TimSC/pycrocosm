import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from replicate.extracts import export_db_extract, find_db_extract

class Command(BaseCommand):
	help = ("Write a stored database extract to a file, in the format its name ends with: "
		".osm, .o5m, .pbf or .json, optionally followed by .gz.")

	def add_arguments(self, parser):
		parser.add_argument("--id", type=int, help="The extract's ID")
		parser.add_argument("--name", help="The extract's name, if unique")
		parser.add_argument("--out", required=True, metavar="FILE", help="The file to write")
		parser.add_argument("--no-edit-ids", action="store_true",
			help="Leave the extract's edit activity checkpoint out of the file header, "
				"which is needed to import it again as an extract that can be kept up to date")

	def handle(self, *args, **options):
		begin = time.time()
		try:
			extract = find_db_extract(options["id"], options["name"])
		except ValueError as err:
			raise CommandError(str(err))
		try:
			export_db_extract(extract["id"], options["out"], not options["no_edit_ids"])
		except Exception as err:
			raise CommandError("Could not export the extract: {}".format(pgmap_error_text(err)))
		self.stdout.write(self.style.SUCCESS("Exported extract {} to {} in {}".format(
			extract["id"], options["out"], format_elapsed(time.time() - begin))))
