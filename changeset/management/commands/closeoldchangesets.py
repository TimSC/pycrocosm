# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.core.management.base import BaseCommand, CommandError
import pgmap
import time
from querymap.views import p

class Command(BaseCommand):
	help = 'Close old changesets'

	def add_arguments(self, parser):
		pass

	def handle(self, *args, **options):
		t = p.GetTransaction("EXCLUSIVE")
		errStr = pgmap.PgMapError()

		whereBeforeTimestamp = int(time.time()) - (24 * 60 * 60)
		closedTimestamp = int(time.time())

		value = t.CloseChangesetsOlderThan(whereBeforeTimestamp, closedTimestamp,
			errStr)

		t.Commit()
		
		self.stdout.write(self.style.SUCCESS('All done!'))


