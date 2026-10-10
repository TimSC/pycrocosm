"""Keeps a scheduled command from starting while its previous run is still going.

Each command holds a lock named after itself for as long as it runs. The lock
is an advisory lock in the settings database, so it is seen by every process
and container that uses the same database, and the database lets go of it by
itself if the command dies. With a settings database other than PostgreSQL it
is a lock on a file instead, which only processes on the same machine share.
"""
import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from functools import wraps

from django.db import connection

def lock_number(name):
	"""The number PostgreSQL knows a named lock by: any 64 bit integer, the same every time."""
	digest = hashlib.sha256(("pycrocosm command " + name).encode("utf-8")).digest()
	return int.from_bytes(digest[:8], "big", signed=True)

@contextmanager
def single_run(name, use_database=None):
	"""Hold the lock of this name while the block runs.

	Gives True if the lock was free and is now held, or False, at once and
	without waiting, if another process holds it.
	"""
	if use_database is None:
		use_database = connection.vendor == "postgresql"
	if use_database:
		with connection.cursor() as cursor:
			cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_number(name)])
			held = cursor.fetchone()[0]
		try:
			yield held
		finally:
			if held:
				with connection.cursor() as cursor:
					cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_number(name)])
		return

	path = os.path.join(tempfile.gettempdir(), "pycrocosm-{}.lock".format(name))
	with open(path, "w") as handle:
		try:
			fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except OSError:
			yield False
			return
		try:
			yield True
		finally:
			fcntl.flock(handle, fcntl.LOCK_UN)

def one_at_a_time(handle):
	"""For the handle method of a management command: a run that starts while
	another of the same command is going says so and does nothing."""
	@wraps(handle)
	def wrapped(self, *args, **options):
		name = type(self).__module__.rsplit(".", 1)[-1]
		with single_run(name) as free:
			if not free:
				self.stdout.write("An earlier {} is still running, so this run does nothing".format(name))
				return
			return handle(self, *args, **options)
	return wrapped
