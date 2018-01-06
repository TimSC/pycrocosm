# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.conf import settings
import pgmap

xmlAttribs = pgmap.mapstringstring({
	'version': str(settings.API_VERSION),
	'generator': settings.GENERATOR,
	'copyright': settings.COPYRIGHT,
	'attribution': settings.ATTRIBUTION,
	'license': settings.LICENSE})

