# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from querymap.views import p
import pgmap
import datetime

def history(request):
	t = p.GetTransaction("ACCESS SHARE")
	uid = 0 #Corresponds to null user ID for no filtering

	changesets = pgmap.vectorchangeset()
	errStr = pgmap.PgMapError()
	isOpenOnly = False
	isClosedOnly = False
	ok = t.GetChangesets(changesets, uid, -1, -1, isOpenOnly, isClosedOnly, errStr)

	t.Commit()

	changesetLi = []
	for i in range(len(changesets)):
		changesetLi.append(changesets[i])

	return render(request, 'objectinfo/history.html', {'changesets': changesetLi})

def changeset(request, changesetId):
	t = p.GetTransaction("ACCESS SHARE")

	changeset = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ok = t.GetChangeset(int(changesetId), changeset, errStr)

	t.Commit()

	return render(request, 'objectinfo/changeset.html', {'changeset': changeset})

