import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from pycrocosm.singlerun import one_at_a_time
from replicate.extracts import list_db_extracts, update_db_extract

class Command(BaseCommand):
	help = ("Bring stored database extracts up to date with the map. Without options, only "
		"the extracts enabled for automatic updates are updated.")

	def add_arguments(self, parser):
		parser.add_argument("--all", action="store_true",
			help="Update every extract, whether or not it is enabled for automatic updates")
		parser.add_argument("--id", action="append", type=int, default=[], metavar="ID",
			help="Update this extract, whether or not it is enabled for automatic updates. "
				"May be given more than once.")

	@one_at_a_time
	def handle(self, *args, **options):
		if options["all"] and options["id"]:
			raise CommandError("Give --all or --id, not both")
		extracts = list_db_extracts()
		if options["id"]:
			known = set(extract["id"] for extract in extracts)
			missing = [i for i in options["id"] if i not in known]
			if missing:
				raise CommandError("No extract with ID {}".format(", ".join(str(i) for i in missing)))
			selected = [extract for extract in extracts if extract["id"] in options["id"]]
		elif options["all"]:
			selected = extracts
		else:
			selected = [extract for extract in extracts if extract["auto_update"]]

		updated, current, failed = 0, 0, 0
		for extract in selected:
			label = "Extract {}".format(extract["id"])
			if extract["name"]:
				label += " ({})".format(extract["name"])
			start = time.time()
			try:
				# One extract at a time, each committing its own work: a failure
				# leaves the others updated. An extract with an update URL is
				# brought up to date from that server, the rest from this map.
				outcome = update_db_extract(extract["id"])
			except Exception as err:
				message = pgmap_error_text(err)
				self.stdout.write(self.style.ERROR("{}: failed, {}".format(label, message)))
				failed += 1
				continue
			if outcome.startswith("already"):
				self.stdout.write("{}: {}".format(label, outcome))
				current += 1
			else:
				self.stdout.write(self.style.SUCCESS("{}: {} in {}".format(label, outcome, format_elapsed(time.time() - start))))
				updated += 1

		if len(selected) == 0:
			self.stdout.write("No extracts to update" if options["all"] or options["id"] else
				"No extracts are enabled for automatic updates")
		else:
			self.stdout.write("{} updated, {} already up to date, {} failed".format(updated, current, failed))
		if failed:
			raise CommandError("{} extract{} could not be updated".format(failed, "" if failed == 1 else "s"))
