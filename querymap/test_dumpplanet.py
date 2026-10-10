import io
import os
import re
import shutil
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

import pgmap
from pycrocosm.mapdb import get_pgmap

class DumpPlanetTestCase(SimpleTestCase):
	"""The manage.py command that writes a planet dump.

	Dumping reads the whole map, which on a real one takes hours or days, so
	no test does it. pgmap's DumpToFile is replaced by a stand-in that records
	how it was called and writes a small file the way pgmap would. What is
	tested is the command: where it writes, the name it gives, and what it refuses.
	"""

	def setUp(self):
		self.folder = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.folder, True)
		self.calls = []
		calls = self.calls

		def stand_in(transaction, filename, edit_ids):
			calls.append((filename, edit_ids))
			if not re.search(r"\.(osm|o5m|pbf|json)(\.gz)?$", filename):
				raise RuntimeError("Standard exception: Output file name must end in a known format")
			with open(filename, "wb") as out:
				out.write(b"map data")
			os.chmod(filename, 0o600) # As pgmap leaves its files
		patcher = patch.object(pgmap.PgTransaction, "DumpToFile", stand_in)
		patcher.start()
		self.addCleanup(patcher.stop)

	def dump(self, *arguments):
		out = io.StringIO()
		call_command("dumpplanet", *arguments, stdout=out, no_color=True)
		return out.getvalue()

	def test_default_settings(self):
		self.assertEqual(settings.PLANET_DUMP_DIR, os.path.join(settings.BASE_DIR, "static", "planet"))
		self.assertEqual(settings.PLANET_DUMP_FILENAME, "fosm-planet_%Y%m%d%H%M%S.o5m")

	def test_dump_to_configured_folder(self):
		# The folder is made if it is not there
		target = os.path.join(self.folder, "static", "planet")
		with override_settings(PLANET_DUMP_DIR=target):
			message = self.dump()
		names = os.listdir(target)
		self.assertEqual(len(names), 1)
		self.assertRegex(names[0], r"^fosm-planet_\d{14}\.o5m$")
		path = os.path.join(target, names[0])
		# pgmap is asked for that file, with the edit IDs in its header
		self.assertEqual(self.calls, [(path, True)])
		self.assertRegex(message, r"^Wrote {} \(8 bytes\) in \d+\.\ds\n$".format(re.escape(path)))
		# Anyone may read a published dump
		self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

	def test_name_and_folder_options(self):
		with override_settings(PLANET_DUMP_DIR=self.folder, PLANET_DUMP_FILENAME="dated-%Y.osm.gz"):
			self.dump()
		self.assertRegex(os.listdir(self.folder)[0], r"^dated-\d{4}\.osm\.gz$")
		other = os.path.join(self.folder, "elsewhere")
		self.dump("--dir", other, "--filename", "planet.pbf")
		self.assertEqual(os.listdir(other), ["planet.pbf"])
		self.assertEqual(self.calls[-1], (os.path.join(other, "planet.pbf"), True))

	def test_refused_dumps(self):
		for pattern, message in (("sub/planet.o5m", "must not include a folder"),
			("../planet.o5m", "must not include a folder"), ("/tmp/planet.o5m", "must not include a folder")):
			with self.assertRaisesRegex(CommandError, message, msg=pattern):
				self.dump("--dir", self.folder, "--filename", pattern)
		self.assertEqual(self.calls, [])
		# What pgmap refuses is reported without its wrapping
		with self.assertRaisesRegex(CommandError, "^Dump failed: Output file name must end in a known format$"):
			self.dump("--dir", self.folder, "--filename", "planet.txt")
		self.assertEqual(os.listdir(self.folder), [])

		# A dump is never written over an earlier one
		existing = os.path.join(self.folder, "same.o5m")
		with open(existing, "wb") as handle:
			handle.write(b"earlier dump")
		calls = len(self.calls)
		with self.assertRaisesRegex(CommandError, "already exists"):
			self.dump("--dir", self.folder, "--filename", "same.o5m")
		with open(existing, "rb") as handle:
			self.assertEqual(handle.read(), b"earlier dump")
		self.assertEqual(len(self.calls), calls)

		with self.assertRaisesRegex(CommandError, "Could not create"):
			self.dump("--dir", os.path.join(existing, "below a file"), "--filename", "planet.o5m")


class DumpToFileTestCase(SimpleTestCase):
	def test_unusable_name_is_refused_before_reading_the_map(self):
		folder = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, folder, True)
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			for name in ("planet.txt", "planet", "planet.o5m.zip"):
				with self.assertRaisesRegex(Exception, "must end in"):
					t.DumpToFile(os.path.join(folder, name), True)
		finally:
			t.Abort()
		self.assertEqual(os.listdir(folder), [])
