from django.core.management.base import BaseCommand, CommandError
from pycrocosm.singlerun import one_at_a_time
import pgmap
import time
from pycrocosm.mapdb import get_pgmap

class Command(BaseCommand):
	help = 'Close old changesets'

	def add_arguments(self, parser):
		pass

	@one_at_a_time
	def handle(self, *args, **options):
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		errStr = pgmap.PgMapError()

		whereBeforeTimestamp = int(time.time()) - (24 * 60 * 60)
		closedTimestamp = int(time.time())

		value = t.CloseChangesetsOlderThan(whereBeforeTimestamp, closedTimestamp,
			errStr)

		t.Commit()
		
		self.stdout.write(self.style.SUCCESS('All done!'))


