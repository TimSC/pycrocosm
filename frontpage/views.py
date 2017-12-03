# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render, reverse
import pgmap
from querymap.views import p

# Create your views here.

def index(request):
	t = p.GetTransaction(b"ACCESS SHARE")

	errStr = pgmap.PgMapError()
	value = int(t.GetMetaValue("readonly".encode('utf-8'), errStr))
	dbStatus = "OK"
	if value != 0:
		dbStatus = "Read only"

	t.Commit()

	return render(request, 'frontpage/index.html', {'db_status': dbStatus})

