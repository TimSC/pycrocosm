import datetime
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pycrocosm.singlerun import one_at_a_time
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap

class Command(BaseCommand):
	help = ("Write the whole map to a planet dump file. The folder and the pattern for the "
		"file name, which also selects the format, are PLANET_DUMP_DIR and PLANET_DUMP_FILENAME "
		"in the settings.")

	def add_arguments(self, parser):
		parser.add_argument("--dir", help="Folder to write to, instead of PLANET_DUMP_DIR")
		parser.add_argument("--filename", help="File name pattern, instead of PLANET_DUMP_FILENAME; "
			"strftime codes stand for the time the dump starts, in UTC")

	@one_at_a_time
	def handle(self, *args, **options):
		folder = options["dir"] or settings.PLANET_DUMP_DIR
		pattern = options["filename"] or settings.PLANET_DUMP_FILENAME
		started = datetime.datetime.now(datetime.timezone.utc)
		name = started.strftime(pattern)
		if name != os.path.basename(name) or name in ("", ".", ".."):
			raise CommandError("The dump file name must not include a folder: {}".format(name))
		path = os.path.join(folder, name)
		if os.path.exists(path):
			# Two dumps in the same second, or a pattern without the time in it
			raise CommandError("{} already exists".format(path))
		try:
			os.makedirs(folder, exist_ok=True)
		except OSError as err:
			raise CommandError("Could not create {}: {}".format(folder, err))

		begin = time.time()
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			# The file is given its name only when complete, so a dump that is
			# being written, or that failed, is never offered for download.
			t.DumpToFile(path, True)
		except Exception as err:
			message = str(err).replace("Standard runtime exception: ", "").replace("Standard exception: ", "")
			raise CommandError("Dump failed: {}".format(message))
		finally:
			common.abort_transaction(t) # Nothing was changed in the map
		# A dump is for publishing, so anyone may read it; pgmap makes its
		# files readable by their owner alone.
		os.chmod(path, 0o644)
		self.stdout.write(self.style.SUCCESS("Wrote {} ({} bytes) in {:.1f}s".format(
			path, os.path.getsize(path), time.time() - begin)))
