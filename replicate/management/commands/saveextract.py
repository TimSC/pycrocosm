import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from replicate.extracts import list_db_extracts, save_db_extract

class Command(BaseCommand):
	help = ("Save a new database extract: a snapshot of the map within a rectangle, or within a polygon "
		"given as WKT. Prints the new extract's ID.")

	def add_arguments(self, parser):
		parser.add_argument("--bbox", metavar="WEST,SOUTH,EAST,NORTH",
			help="The rectangle the extract covers, for example -1.078,50.788,-1.074,50.790")
		parser.add_argument("--wkt", metavar="WKT",
			help="The polygon the extract covers, longitude before latitude, for example "
				"\"POLYGON((-1.08 50.78, -1.06 50.78, -1.07 50.80, -1.08 50.78))\"")
		parser.add_argument("--wkt-file", metavar="FILE", help="A file holding the polygon as WKT")
		parser.add_argument("--name", default="",
			help="A name for the extract, which must be unique, so that it can be chosen by name")

	def handle(self, *args, **options):
		begin = time.time()
		given = [key for key in ("bbox", "wkt", "wkt_file") if options[key]]
		if len(given) != 1:
			raise CommandError("Give one of --bbox, --wkt and --wkt-file")

		bbox, wkt = None, None
		if options["bbox"]:
			try:
				bbox = [float(value) for value in options["bbox"].split(",")]
			except ValueError:
				bbox = []
			if len(bbox) != 4:
				raise CommandError("--bbox must be four numbers: west,south,east,north")
		elif options["wkt"]:
			wkt = options["wkt"]
		else:
			try:
				with open(options["wkt_file"]) as handle:
					wkt = handle.read()
			except OSError as err:
				raise CommandError("Cannot read {}: {}".format(options["wkt_file"], err))

		name = options["name"].strip()
		if name and any(extract["name"] == name for extract in list_db_extracts()):
			raise CommandError("An extract named {} already exists".format(name))
		try:
			extract_id = save_db_extract(bbox, name, wkt)
		except Exception as err:
			raise CommandError("Could not save the extract: {}".format(pgmap_error_text(err)))
		self.stdout.write(self.style.SUCCESS("Saved database extract {} in {}".format(
			extract_id, format_elapsed(time.time() - begin))))
