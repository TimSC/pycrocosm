# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.conf import settings
import pgmap
import sys
import threading

TEST = 'test' in sys.argv
if TEST:
	ACTIVE_DB = "PREFIX_TEST"
else:
	ACTIVE_DB = "PREFIX_MOD"

_thread_local = threading.local()

def Escape(st):
	return st.replace('"','\\"').replace("'","\\'")

def make_connection_string():
	mapDbSettings = settings.MAP_DATABASE
	return ("dbname='{}' user='{}' password='{}' hostaddr='{}' port='{}'".format(Escape(mapDbSettings["NAME"]),
		Escape(mapDbSettings["USER"]), Escape(mapDbSettings["PASSWORD"]), Escape(mapDbSettings["HOST"]), Escape(mapDbSettings["PORT"])))

def make_pgmap_limits():
	wayNodesMaximum = int(getattr(settings, "WAYNODES_MAXIMUM", 2000000))
	relationMembersMaximum = int(getattr(settings, "RELATION_MEMBERS_MAXIMUM", 3200000))
	return {
		"max_bytes": int(getattr(settings, "XML_UPLOAD_MAXIMUM_BYTES", 10 * 1024 * 1024)),
		"max_depth": int(getattr(settings, "PGMAP_XML_MAX_DEPTH", 16)),
		"max_objects": int(getattr(settings, "CHANGESETS_MAXIMUM_ELEMENTS", 100000)),
		"max_tags_per_object": int(getattr(settings, "PGMAP_XML_MAX_TAGS_PER_OBJECT", 10000)),
		"max_members_per_object": max(wayNodesMaximum, relationMembersMaximum),
		"max_attributes_per_element": int(getattr(settings, "PGMAP_XML_MAX_ATTRIBUTES_PER_ELEMENT", 32)),
		"max_attribute_bytes": int(getattr(settings, "PGMAP_XML_MAX_ATTRIBUTE_BYTES", 4096)),
	}

def make_pgmap():
	mapDbSettings = settings.MAP_DATABASE
	return pgmap.PgMap(make_connection_string(),
		str(mapDbSettings["PREFIX"]), str(mapDbSettings[ACTIVE_DB]),
		str(mapDbSettings["PREFIX_MOD"]), str(mapDbSettings["PREFIX_TEST"]),
		make_pgmap_limits())

def get_pgmap():
	p = getattr(_thread_local, "pgmap", None)
	if p is None or not p.Ready():
		p = make_pgmap()
		_thread_local.pgmap = p
	return p
