# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.conf import settings
import pgmap
import sys
import datetime
PY3 = sys.version_info >= (3,)
if not PY3:
	import pytz

xmlAttribs = pgmap.mapstringstring({
	'version': str(settings.API_VERSION),
	'generator': settings.GENERATOR,
	'copyright': settings.COPYRIGHT,
	'attribution': settings.ATTRIBUTION,
	'license': settings.LICENSE})

def get_utc_posix_timestamp(dt):
	if dt.tzinfo is None:
		raise ValueError("datetime object should not be naive")

	if PY3:
		return dt.timestamp()
	else:
		return (dt - datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()

