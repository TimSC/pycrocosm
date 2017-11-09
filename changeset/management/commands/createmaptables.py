from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Create map data tables'

	def add_arguments(self, parser):
		pass

	def handle(self, *args, **options):
		
		admin = p.GetAdmin(b"EXCLUSIVE")

		errStr = pgmap.PgMapError()
		ok = admin.CreateMapTables(errStr)
		if not ok:
			self.stdout.write(self.style.ERROR(errStr.errStr))
			return

		admin.Commit()

		self.stdout.write(self.style.SUCCESS('All done!'))

