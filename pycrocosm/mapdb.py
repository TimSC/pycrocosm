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

def make_pgmap():
	mapDbSettings = settings.MAP_DATABASE
	return pgmap.PgMap(make_connection_string(),
		str(mapDbSettings["PREFIX"]), str(mapDbSettings[ACTIVE_DB]),
		str(mapDbSettings["PREFIX_MOD"]), str(mapDbSettings["PREFIX_TEST"]))

def get_pgmap():
	p = getattr(_thread_local, "pgmap", None)
	if p is None or not p.Ready():
		p = make_pgmap()
		_thread_local.pgmap = p
	return p
