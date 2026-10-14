import time

from django.core.management.base import BaseCommand, CommandError
from pycrocosm.common import format_elapsed, pgmap_error_text
from replicate.extracts import import_db_extract, list_db_extracts

class Command(BaseCommand):
	help = ("Store a map file as a new database extract. The file's format follows its name: "
		".osm, .o5m, .pbf or .json, optionally followed by .gz. The extract's area and edit activity "
		"checkpoint come from the file unless given here.")

	def add_arguments(self, parser):
		parser.add_argument("--in", dest="source", required=True, metavar="FILE", help="The map file")
		parser.add_argument("--name", default="", help="A name for the new extract, which must be unique")
		parser.add_argument("--bbox", metavar="WEST,SOUTH,EAST,NORTH",
			help="The rectangle the extract covers, in place of the file's bounds")
		parser.add_argument("--wkt", help="The polygon the extract covers, as WKT, in place of the file's bounds")
		parser.add_argument("--wkt-file", metavar="FILE", help="A file holding the polygon as WKT")
		parser.add_argument("--buffer", type=float, default=0, metavar="METRES",
			help="Grow the --wkt or --wkt-file boundary outwards by this many metres first")
		parser.add_argument("--edit-activity-id", type=int, default=-1,
			help="The edit activity ID the contents are current to, in place of the file's")
		parser.add_argument("--atomic-edit-id", type=int, default=-1,
			help="The atomic edit ID the contents are current to, in place of the file's")

	def handle(self, *args, **options):
		begin = time.time()
		if sum(1 for key in ("bbox", "wkt", "wkt_file") if options[key]) > 1:
			raise CommandError("Give at most one of --bbox, --wkt and --wkt-file")
		if options["buffer"] and not (options["wkt"] or options["wkt_file"]):
			raise CommandError("--buffer grows a --wkt or --wkt-file boundary, and needs one")
		bbox = None
		if options["bbox"]:
			try:
				bbox = [float(value) for value in options["bbox"].split(",")]
			except ValueError:
				bbox = []
			if len(bbox) != 4:
				raise CommandError("--bbox must be four numbers: west,south,east,north")
		wkt = options["wkt"]
		if options["wkt_file"]:
			try:
				with open(options["wkt_file"]) as handle:
					wkt = handle.read()
			except OSError as err:
				raise CommandError("Cannot read {}: {}".format(options["wkt_file"], err))
		if (options["edit_activity_id"] < 0) != (options["atomic_edit_id"] < 0):
			raise CommandError("Give both --edit-activity-id and --atomic-edit-id, or neither")
		name = options["name"].strip()
		if name and any(extract["name"] == name for extract in list_db_extracts()):
			raise CommandError("An extract named {} already exists".format(name))
		try:
			extract_id, nodes, ways, relations = import_db_extract(options["source"], name, bbox, wkt,
				(options["edit_activity_id"], options["atomic_edit_id"]), options["buffer"])
		except Exception as err:
			raise CommandError("Could not import {}: {}".format(options["source"], pgmap_error_text(err)))
		self.stdout.write(self.style.SUCCESS("Imported {} as extract {}: {} nodes, {} ways, {} relations, in {}".format(
			options["source"], extract_id, nodes, ways, relations, format_elapsed(time.time() - begin))))
