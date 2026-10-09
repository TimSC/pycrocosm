# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.http import HttpResponse
from django.views.decorators.gzip import gzip_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from rest_framework.decorators import api_view
from pycrocosm.ratelimit import rate_limit
import time

from . import ql, output
from .ql import QueryError
from .interpreter import execute, evaluate

# Queries are limited per client address. Zero for either setting turns the limit off.
overpass_rate_limit = rate_limit("overpass", "OVERPASS_RATE_LIMIT_REQUESTS",
	"OVERPASS_RATE_LIMIT_WINDOW_SECONDS", methods=('GET', 'POST'), defaults=(60, 60))

def error_response(err):
	return HttpResponse("Error: {}\n".format(err), status=err.status, content_type="text/plain; charset=utf-8")

def allow_any_origin(response):
	# Web clients such as Overpass Turbo call from their own sites. The data is
	# public and no login is used, so any site may read the answer.
	response["Access-Control-Allow-Origin"] = "*"
	return response

# ****** Overpass QL ******

@gzip_page #This page contains no secrets, so compression is safe against BREACH.
@csrf_exempt #Not a form of this site: there is no session to protect.
@require_http_methods(["GET", "POST", "OPTIONS"])
@overpass_rate_limit
def interpreter(request):
	"""Run an Overpass QL query given as the data parameter, or as the request body."""
	if request.method == "OPTIONS":
		response = HttpResponse(status=204)
		response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
		response["Access-Control-Allow-Headers"] = "Content-Type"
		return allow_any_origin(response)

	params = request.GET
	if request.method == "POST" and "data" in request.POST:
		params = request.POST
	if "data" in params:
		query = params["data"]
	elif request.method == "POST":
		try:
			query = request.body.decode("utf-8")
		except UnicodeDecodeError:
			query = None
	else:
		query = ""

	try:
		if query is None:
			raise QueryError("the query is not valid UTF-8")
		if query.strip() == "":
			raise QueryError("no query was given; send Overpass QL as the data parameter")
		content, content_type = execute(query, params.get("bbox") or request.GET.get("bbox"))
	except QueryError as err:
		return allow_any_origin(error_response(err))
	return allow_any_origin(HttpResponse(content, content_type=content_type))

# ****** XAPI ******
# XAPI requests are translated to the statements of an Overpass query.

def ParseBrackets(queryStr):
	
	c = 0
	skipNext = False
	depth = 0
	buff = []
	fragments = []
	while c < len(queryStr):
		if skipNext:
			skipNext = False
			if depth >= 1:
				buff.append(queryStr[c])
		elif queryStr[c] == '\\':
			if depth >= 1:
				buff.append(queryStr[c])
			else:
				skipNext = True
		elif queryStr[c] == '[':
			if depth >= 1:
				buff.append(queryStr[c])	
			depth += 1
		elif queryStr[c] == ']':
			depth -= 1
			if depth >= 1:
				buff.append(queryStr[c])
			elif depth < 0:
				raise ValueError("Unexpected closing bracket")
			else:
				fragments.append("".join(buff))
				buff = []
		else:
			buff.append(queryStr[c])
		c += 1

	return fragments

def ParseFragment(frag):

	c = 0
	skipNext = False
	fragments = []
	buff = []
	while c < len(frag):
		if skipNext:
			skipNext = False
		elif frag[c] == '\\':
			skipNext = True
		elif frag[c] == '=':
			fragments.append("".join(buff))
			buff = []
		else:
			buff.append(frag[c])

		c += 1	

	fragments.append("".join(buff))

	return fragments

XAPI_TYPES = (('*', ("node", "way", "relation")), ('node', ("node",)), ('way', ("way",)),
	('relation', ("relation",)))

def xapi_to_script(queryStr):
	"""The Overpass statements for an XAPI request such as way[highway=primary][bbox=w,s,e,n]."""
	types = None
	for name, kinds in XAPI_TYPES:
		if queryStr[:len(name)] == name:
			types = kinds
			queryStr = queryStr[len(name):]
	if types is None:
		raise QueryError("Object type not recognized")

	try:
		fragments = ParseBrackets(queryStr)
	except ValueError as err:
		raise QueryError(str(err))

	filters = []
	for frag in fragments:
		fragParts = ParseFragment(frag)
		if fragParts[0] == "bbox" and len(fragParts) >= 2:
			try:
				bbox = list(map(float, fragParts[1].split(",")))
			except ValueError:
				bbox = []
			if len(bbox) != 4:
				raise QueryError("Invalid bbox")
			# XAPI gives west,south,east,north
			try:
				filters.append(ql.BboxQuery(*ql.Parser("").bbox((bbox[1], bbox[0], bbox[3], bbox[2]))))
			except QueryError:
				raise QueryError("Invalid bbox")
		elif len(fragParts) >= 2:
			if fragParts[1] == '*':
				filters.append(ql.HasKv(ql.HasKv.EXISTS, fragParts[0]))
			else:
				filters.append(ql.HasKv(ql.HasKv.EQUALS, fragParts[0], fragParts[1]))

	if len(filters) == 0:
		raise QueryError("Specify either a bbox or a query key (at least)")

	# The objects asked for, with everything needed to draw them
	return ql.OsmScript([
		ql.Union([ql.Query(types, filters), ql.Recurse("down-rel")]),
		ql.Print(mode="meta"),
	])

def xapi(request, queryStr):
	try:
		result = evaluate(xapi_to_script(queryStr))
	except QueryError as err:
		return error_response(err)
	legal = [("copyright", settings.COPYRIGHT), ("attribution", settings.ATTRIBUTION),
		("license", settings.LICENSE)]
	content = output.write_xml(result, settings.GENERATOR, settings.COPYRIGHT, time.time(), legal)
	return HttpResponse(content, content_type='text/xml')

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def xapi1(request, queryStr):
	return xapi(request, queryStr)

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def xapi2(request):
	return xapi(request, request.META['QUERY_STRING'])
