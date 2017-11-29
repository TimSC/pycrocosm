from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Get metadata variable'

	def add_arguments(self, parser):
		parser.add_argument('key', nargs='+', type=str)

	def handle(self, *args, **options):
		t = p.GetTransaction(b"ACCESS SHARE")
		errStr = pgmap.PgMapError()

		value = t.GetMetaValue(options['key'][0].encode('utf-8'), 
			errStr)

		t.Commit()
		
		self.stdout.write(self.style.SUCCESS('All done! ' + value))


