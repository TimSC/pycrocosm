from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Update next IDs'

	def add_arguments(self, parser):
		#parser.add_argument('poll_id', nargs='+', type=int)
		pass

	def handle(self, *args, **options):
		
		t = p.GetTransaction(b"EXCLUSIVE")

		errStr = pgmap.PgMapError()
		ok = t.UpdateNextIds(errStr)
		if not ok:
			self.stdout.write(self.style.ERROR(errStr.errStr))

		t.Commit()

		self.stdout.write(self.style.SUCCESS('All done!'))

