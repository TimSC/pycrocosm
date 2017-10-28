from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Reset active tables to empty'

	def add_arguments(self, parser):
		pass

	def handle(self, *args, **options):
		
		t = p.GetTransaction(b"EXCLUSIVE")

		errStr = pgmap.PgMapError()
		ok = t.ResetActiveTables(errStr)
		if not ok:
			self.stdout.write(self.style.ERROR(errStr.errStr))

		t.Commit()

		self.stdout.write(self.style.SUCCESS('All done!'))

