# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from querymap.views import p
import pgmap
import datetime
import io

# Create your views here.
def augdiff(request):
	t = p.GetTransaction("ACCESS SHARE")
	uid = 0 #Corresponds to null user ID for no filtering

	#changesets = pgmap.vectorchangeset()
	startTimestamp = 0
	bboxSet = False
	bbox = pgmap.vectord()
	errStr = pgmap.PgMapError()
	fiOut = pgmap.PgStringWrap()
	#isOpenOnly = False
	#isClosedOnly = False
	ok = t.QueryAugDiff(startTimestamp,
		bboxSet,
		bbox,
		fiOut,
		errStr);

	t.Commit()

	return HttpResponse(fiOut.str)

