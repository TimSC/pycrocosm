# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.core.management.base import BaseCommand, CommandError
import pgmap
from querymap.views import p

class Command(BaseCommand):
	help = 'Get metadata variable'

	def add_arguments(self, parser):
		parser.add_argument('key', nargs='+', type=str)

	def handle(self, *args, **options):
		t = p.GetTransaction("ACCESS SHARE")
		errStr = pgmap.PgMapError()

		value = t.GetMetaValue(options['key'][0], 
			errStr)

		t.Commit()
		
		self.stdout.write(self.style.SUCCESS('All done! ' + value))


