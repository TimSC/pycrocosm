# -*- coding: utf-8 -*-
"""Runs Overpass queries: parse, evaluate against the map, write the result."""
from __future__ import unicode_literals
import time

from django.conf import settings
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap
from . import ql, output
from .ql import QueryError
from .evaluator import Evaluator, Limits, MapSource

def get_limits(requested_timeout=None):
	"""The limits for one query, which may ask for a shorter timeout than the server's."""
	maximum = getattr(settings, 'OVERPASS_TIMEOUT_MAXIMUM', 180)
	timeout = min(getattr(settings, 'OVERPASS_TIMEOUT_DEFAULT', 180), maximum)
	if requested_timeout is not None:
		# A longer timeout than the server allows is cut down, not refused
		timeout = max(1, min(requested_timeout, maximum))
	return Limits(timeout,
		getattr(settings, 'OVERPASS_ELEMENTS_MAXIMUM', 100000),
		settings.OVERPASS_AREA_MAXIMUM)

def parse_url_bbox(text):
	"""The bbox URL parameter, which is west,south,east,north, as (south, west, north, east)."""
	try:
		west, south, east, north = [float(v) for v in text.split(",")]
	except ValueError:
		raise QueryError("the bbox parameter must be four numbers: west,south,east,north")
	return ql.Parser("").bbox((south, west, north, east))

def evaluate(script, limits=None):
	"""Run a parsed query, returning what its out statements produced."""
	if limits is None:
		limits = get_limits(script.timeout)
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		return Evaluator(MapSource(t, limits), limits).run(script)
	finally:
		# Nothing is written, and a query that failed has left the transaction unusable
		common.abort_transaction(t)

def execute(text, url_bbox=None):
	"""Run a query given as Overpass QL text.

	Returns the response body and its content type. Raises QueryError, whose
	status says how to answer, if the query cannot be run.
	"""
	script = ql.parse(text)
	if script.bbox_from_url:
		if not url_bbox:
			raise QueryError("the query has a [bbox] setting, but no bbox parameter came with it")
		script.bbox = parse_url_bbox(url_bbox)
	result = evaluate(script)
	if script.output == "json":
		return output.write_json(result, settings.GENERATOR, settings.COPYRIGHT, time.time()), "application/json"
	return output.write_xml(result, settings.GENERATOR, settings.COPYRIGHT, time.time()), "application/osm3s+xml"
