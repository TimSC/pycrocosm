import time

from django.core.management.base import BaseCommand, CommandError
from replicate.extracts import list_db_extracts, change_db_extract

class Command(BaseCommand):
	help = ("Bring stored database extracts up to date with the map. Without options, only "
		"the extracts enabled for automatic updates are updated.")

	def add_arguments(self, parser):
		parser.add_argument("--all", action="store_true",
			help="Update every extract, whether or not it is enabled for automatic updates")
		parser.add_argument("--id", action="append", type=int, default=[], metavar="ID",
			help="Update this extract, whether or not it is enabled for automatic updates. "
				"May be given more than once.")

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

		updated, current, skipped, failed = 0, 0, 0, 0
		for extract in selected:
			label = "Extract {}".format(extract["id"])
			if extract["name"]:
				label += " ({})".format(extract["name"])
			if extract["update_url"]:
				# The setting exists ahead of the feature
				self.stdout.write(self.style.WARNING("{}: skipped, updating from another API ({}) "
					"is not implemented yet".format(label, extract["update_url"])))
				skipped += 1
				continue
			if extract["up_to_date"]:
				self.stdout.write("{}: already up to date".format(label))
				current += 1
				continue
			start = time.time()
			try:
				# One transaction for each extract: a failure leaves the others updated
				change_db_extract("UpdateExtract", extract["id"])
			except Exception as err:
				message = str(err).replace("Standard runtime exception: ", "")
				self.stdout.write(self.style.ERROR("{}: failed, {}".format(label, message)))
				failed += 1
				continue
			self.stdout.write(self.style.SUCCESS("{}: updated in {:.1f}s".format(label, time.time() - start)))
			updated += 1

		if len(selected) == 0:
			self.stdout.write("No extracts to update" if options["all"] or options["id"] else
				"No extracts are enabled for automatic updates")
		else:
			self.stdout.write("{} updated, {} already up to date, {} skipped, {} failed".format(
				updated, current, skipped, failed))
		if failed:
			raise CommandError("{} extract{} could not be updated".format(failed, "" if failed == 1 else "s"))
