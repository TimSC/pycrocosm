from django.shortcuts import render, reverse
import pgmap
from pycrocosm.mapdb import get_pgmap

# Create your views here.

def index(request):
	t = get_pgmap().GetTransaction("ACCESS SHARE")

	errStr = pgmap.PgMapError()
	value = int(t.GetMetaValue("readonly", errStr))
	dbStatus = "OK"
	if value != 0:
		dbStatus = "Read only"
	# The examples of area searches are shown when ways and relations are
	# found by their stored boxes, which makes such searches quick
	useBboxInQuery = bool(t.UseBboxInQuery())

	t.Commit()

	return render(request, 'frontpage/index.html', {'db_status': dbStatus, 'use_bbox_in_query': useBboxInQuery})

