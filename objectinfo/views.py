# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from querymap.views import p
import pgmap
import datetime

def history(request):
	t = p.GetTransaction(b"ACCESS SHARE")

	changesets = pgmap.vectorchangeset()
	errStr = pgmap.PgMapError()
	ok = t.GetChangesets(changesets, errStr)

	t.Commit()

	changesetLi = []
	for i in range(len(changesets)):
		changesetLi.append(changesets[i])

	return render(request, 'objectinfo/history.html', {'changesets': changesetLi})

