import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from replicate.extracts import change_db_extract, find_db_extract

class Command(BaseCommand):
	help = "Delete a stored database extract and everything it holds. Asks first, unless --yes is given."

	def add_arguments(self, parser):
		parser.add_argument("--id", type=int, help="The extract's ID")
		parser.add_argument("--name", help="The extract's name, if unique")
		parser.add_argument("--yes", action="store_true", help="Delete without asking")

	def handle(self, *args, **options):
		try:
			extract = find_db_extract(options["id"], options["name"])
		except ValueError as err:
			raise CommandError(str(err))
		label = "extract {}".format(extract["id"]) + (" ({})".format(extract["name"]) if extract["name"] else "")
		if not options["yes"]:
			try:
				answer = input("Delete {} and everything it holds? This cannot be undone. Type delete to go ahead: ".format(label))
			except EOFError:
				answer = ""
			if answer.strip() != "delete":
				raise CommandError("Nothing was deleted")
		begin = time.time()
		try:
			change_db_extract("DeleteExtract", extract["id"])
		except Exception as err:
			raise CommandError("Could not delete {}: {}".format(label, pgmap_error_text(err)))
		self.stdout.write(self.style.SUCCESS("Deleted {} in {}".format(label, format_elapsed(time.time() - begin))))
