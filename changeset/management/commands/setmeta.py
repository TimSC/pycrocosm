from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Set metadata variable'

	def add_arguments(self, parser):
		parser.add_argument('key', nargs='+', type=str)
		parser.add_argument('value', nargs='+', type=str)

	def handle(self, *args, **options):
		t = p.GetTransaction("EXCLUSIVE")
		errStr = pgmap.PgMapError()

		ok = t.SetMetaValue(options['key'][0], 
			options['value'][0], 
			errStr)
		if not ok:
			t.Abort()
			raise CommandError(errStr.errStr)

		t.Commit()
		self.stdout.write(self.style.SUCCESS('All done!'))


