# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from oauth_provider import views as oauth_views
from django.views.decorators.csrf import csrf_exempt
import urllib2

# Create your views here.

@csrf_exempt
def request_token(request):

	auth_header = None
	if 'Authorization' in request.META:
		auth_header = request.META['Authorization']
	elif 'HTTP_AUTHORIZATION' in request.META:
		auth_header = request.META['HTTP_AUTHORIZATION']
		del request.META['HTTP_AUTHORIZATION']

	print (auth_header)

	if auth_header is not None:
		# https://stackoverflow.com/a/24729316/4288232
		field, sep, value = auth_header.partition("OAuth ")
		if value:
			items = urllib2.parse_http_list(value)
			opts = urllib2.parse_keqv_list(items)
			if 'oauth_callback' in opts and len(opts['oauth_callback']) == 0:
				opts['oauth_callback'] = 'oob'

			auth_frags = ["OAuth "]
			for i, k in enumerate(opts):
				if i > 0:
					auth_frags.append(u", ")
				auth_frags.append(u"{}=\"{}\"".format(k, opts[k]))

			auth_header = u"".join(auth_frags)

	request.META['Authorization'] = auth_header
	print (request.META['Authorization'])

	return oauth_views.request_token(request)

