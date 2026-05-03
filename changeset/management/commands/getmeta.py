# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.core.management.base import BaseCommand, CommandError
import pgmap
from pycrocosm.mapdb import get_pgmap

class Command(BaseCommand):
	help = 'Get metadata variable'

	def add_arguments(self, parser):
		parser.add_argument('key', nargs='+', type=str)

	def handle(self, *args, **options):
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		errStr = pgmap.PgMapError()

		value = t.GetMetaValue(options['key'][0], 
			errStr)

		t.Commit()
		
		self.stdout.write(self.style.SUCCESS('All done! ' + value))


