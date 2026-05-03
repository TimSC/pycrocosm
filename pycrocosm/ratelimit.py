# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from functools import wraps

def get_client_ip(request):
	return request.META.get('REMOTE_ADDR', '')

def rate_limit(group, limit_setting, window_setting, methods=('POST',)):
	def decorator(view_func):
		@wraps(view_func)
		def wrapped(request, *args, **kwargs):
			if methods is not None and request.method not in methods:
				return view_func(request, *args, **kwargs)

			limit = getattr(settings, limit_setting)
			window = getattr(settings, window_setting)
			if limit <= 0 or window <= 0:
				return view_func(request, *args, **kwargs)

			key = "ratelimit:{}:{}:{}".format(group, get_client_ip(request), request.method)
			cache.add(key, 0, window)
			try:
				count = cache.incr(key)
			except ValueError:
				cache.add(key, 1, window)
				count = 1

			if count > limit:
				response = HttpResponse("Too many requests", status=429, content_type="text/plain")
				response["Retry-After"] = str(window)
				return response

			return view_func(request, *args, **kwargs)
		return wrapped
	return decorator
