# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from migrateusers.models import LegacyAccount
import xml.etree.ElementTree as ET
import common

class Command(BaseCommand):
	help = 'Import legacy accounts into django database tables'

	def add_arguments(self, parser):
		parser.add_argument('infile', type=str)

	def handle(self, *args, **options):

		tree = ET.parse(options['infile'])
		root = tree.getroot()

		for userNode in root:
			self.stdout.write("Importing {}".format(userNode.attrib["display_name"]))
			try:
				existingUser = LegacyAccount.objects.get(uid=int(userNode.attrib["id"]))
			except LegacyAccount.DoesNotExist:

				homeNode = userNode.find("home")

				user = LegacyAccount.objects.create(uid=int(userNode.attrib["id"]),
					username = userNode.attrib["display_name"],
					email = userNode.attrib["email"],
					hashed_password = userNode.attrib["sha256pw"],
					created_at = common.get_utc_posix_timestamp(parse_datetime(userNode.attrib["account_created"])),
					lat = float(homeNode.attrib["lat"]),
					lon = float(homeNode.attrib["lon"]),
					zoom = float(homeNode.attrib["zoom"]),
					)
				user.save()

		self.stdout.write(self.style.SUCCESS('All done!'))

