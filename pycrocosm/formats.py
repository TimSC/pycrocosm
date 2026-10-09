# -*- coding: utf-8 -*-
"""Format suffixes on API paths, such as /api/0.6/map.json."""
from __future__ import unicode_literals
from __future__ import print_function

from django.urls import resolve
from django.views.decorators.csrf import csrf_exempt

# The views decide for themselves whether a request must pass a CSRF check,
# exactly as they do when reached without a suffix.
@csrf_exempt
def with_format(request, apipath, fmt):
	"""Serve an API path that ends in .json or .xml.

	The suffix is removed, the format recorded on the request for
	pycrocosm.common.wants_json, and the request handed to whichever view
	serves the path without the suffix.
	"""
	path = '/' + apipath
	match = resolve(path)
	request.osm_format = fmt
	request.path_info = path
	request.path = path
	return match.func(request, *match.args, **match.kwargs)
