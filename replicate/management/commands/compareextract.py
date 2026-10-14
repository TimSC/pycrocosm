import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from replicate.extracts import compare_db_extracts, find_db_extract, list_db_extracts

class Command(BaseCommand):
	help = ("Compare stored database extracts with fresh map queries of their areas, both read in one "
		"snapshot. Object types, IDs and versions must agree; tags, positions and members are not compared. "
		"Nothing is changed. Finishes with an error status if any extract differs.")

	def add_arguments(self, parser):
		parser.add_argument("--id", type=int, help="The extract's ID")
		parser.add_argument("--name", help="The extract's name, if unique")
		parser.add_argument("--all", action="store_true", help="Compare every extract")

	def report(self, result, label):
		bbox = ",".join(str(value) for value in result.bbox)
		self.stdout.write("{} bbox {}".format(label, bbox))
		for kind, ours, theirs in zip(("nodes", "ways", "relations"), result.extractCounts, result.queryCounts):
			self.stdout.write("{}: extract {}, query {}".format(kind, ours, theirs))
		for difference in result.differences:
			if not difference.extractVersion:
				what = "missing from extract (query version {})".format(difference.queryVersion)
			elif not difference.queryVersion:
				what = "not in query (extract version {})".format(difference.extractVersion)
			else:
				what = "extract version {}, query version {}".format(difference.extractVersion, difference.queryVersion)
			self.stdout.write("{} {}: {}".format(difference.type, difference.objId, what))
		if result.pendingActivity:
			self.stdout.write("Note: the map has edits later than the extract's checkpoint; "
				"run manage.py updateextracts")
		if result.queryModeDiffers:
			self.stdout.write("Note: the extract was saved with a different useBboxInQuery mode from the map's current one")
		if result.Matches():
			self.stdout.write(self.style.SUCCESS("{} matches query".format(label)))
		else:
			self.stdout.write(self.style.ERROR("{} differs from query: {} missing from extract, {} not in query, "
				"{} version mismatches".format(label, result.missingFromExtract, result.notInQuery,
				result.versionMismatches)))

	def handle(self, *args, **options):
		begin = time.time()
		if options["all"] == (options["id"] is not None or bool(options["name"])):
			raise CommandError("Give --id, --name or --all")
		if not options["all"]:
			try:
				chosen = find_db_extract(options["id"], options["name"])
			except ValueError as err:
				raise CommandError(str(err))
		names = {info["id"]: info["name"] for info in list_db_extracts()}
		try:
			results = compare_db_extracts(None if options["all"] else chosen["id"])
		except Exception as err:
			raise CommandError("Could not compare: {}".format(pgmap_error_text(err)))

		differing = 0
		for result in results:
			label = "Extract {}".format(result.extractId)
			if names.get(result.extractId):
				label += " ({})".format(names[result.extractId])
			self.report(result, label)
			if not result.Matches():
				differing += 1
		elapsed = format_elapsed(time.time() - begin)
		if options["all"]:
			self.stdout.write("Checked {} extracts in {}: {} differ from their queries".format(
				len(results), elapsed, differing))
		else:
			self.stdout.write("Checked in {}".format(elapsed))
		if differing:
			raise CommandError("1 extract differs from its query" if differing == 1 else
				"{} extracts differ from their queries".format(differing))
