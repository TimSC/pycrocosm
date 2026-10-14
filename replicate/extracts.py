"""Access to the extracts stored in the map database, through pgmap."""

import datetime

import pgmap

from pycrocosm.mapdb import get_pgmap

def describe_db_extract(info):
	extract = {
		"id": info.extractId,
		"name": info.name,
		# The rectangle around the boundary, and the boundary itself, which may
		# be any polygon, or several (a multipolygon)
		"bbox": list(info.bbox),
		"boundary": info.boundaryWkt,
		"rectangular": bool(info.rectangular),
		"shape": "rectangle" if info.rectangular else
			("multipolygon" if info.boundaryWkt.lstrip().upper().startswith("MULTI") else "polygon"),
		"query_mode": "bbox" if info.useBboxInQuery else "membership",
		"performed_at": datetime.datetime.fromtimestamp(info.performedAt, datetime.timezone.utc),
		"edit_activity_id": info.editActivityId if info.editActivityId >= 0 else None,
		"atomic_edit_id": info.atomicEditId if info.atomicEditId >= 0 else None,
		# Whether this map has edits the extract lacks. That says nothing about an
		# extract kept up from another server's map, so then it is not known.
		"up_to_date": None if info.updateUrl else not info.pendingActivity,
		# What is wanted for keeping the extract up to date; nothing acts on it yet
		"auto_update": info.autoUpdate,
		"update_url": info.updateUrl,
	}
	if info.nodes >= 0:
		extract.update({"nodes": info.nodes, "ways": info.ways, "relations": info.relations})
	return extract

def list_db_extracts():
	"""Describe the extracts stored in the map database, in ID order, without object counts."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		infos = pgmap.vectorextractinfo()
		t.ListExtracts(infos)
		return [describe_db_extract(info) for info in infos]
	finally:
		t.Abort() # Read only; nothing to commit.

def list_db_extracts_with_latest():
	"""The extracts as list_db_extracts gives them, and the map's latest edit
	activity ID and atomic edit ID, both read from the same snapshot."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		infos = pgmap.vectorextractinfo()
		t.ListExtracts(infos)
		latest = t.GetLatestEditIds()
		return ([describe_db_extract(info) for info in infos],
			{"edit_activity_id": latest[0], "atomic_edit_id": latest[1]})
	finally:
		t.Abort() # Read only; nothing to commit.

def get_db_extract(extract_id):
	"""Describe one extract, including its object counts, or None if it does not exist."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		info = pgmap.ExtractInfo()
		if not t.GetExtract(extract_id, info):
			return None
		return describe_db_extract(info)
	finally:
		t.Abort() # Read only; nothing to commit.

def save_db_extract(bbox, name, wkt=None, buffer=0):
	"""Store a snapshot of the map within bbox, or within the polygon given as
	WKT, grown outwards by buffer metres, and return the new extract's ID. A
	boundary pgmap cannot take is refused with the reason."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		if wkt and buffer:
			# The extract's boundary is the grown one; nothing after this needs to know
			wkt = t.BufferExtractBoundary(wkt, float(buffer))
		extract_id = t.SaveExtractWkt(wkt, name) if wkt else t.SaveExtract(bbox, name)
		t.Commit()
		return extract_id
	except BaseException:
		t.Abort()
		raise

def set_db_extract_auto_update(extract_id, enabled, update_url):
	"""Set whether an extract is updated automatically, and the API to update it from (blank for this map)."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		t.SetExtractAutoUpdate(extract_id, "", bool(enabled), update_url)
		t.Commit()
	except BaseException:
		t.Abort()
		raise

def update_db_extract(extract_id, fetch=None):
	"""Bring one extract up to date: from this map, or from the other server
	named by its update URL, without reading this map. fetch, for tests,
	stands in for reading a web address.

	An extract of this map is updated from the edits recorded since its
	checkpoint, which takes time in proportion to those edits. Taking the
	whole extract from the map afresh, which takes time in proportion to its
	size, is kept for three cases: an extract that selects ways and relations
	by their stored bounding boxes, which the recorded edits cannot keep up;
	edits recorded before they held all that an update needs; and when
	EXTRACT_UPDATE_FROM_RECORDS is set to False.

	Returns a short description of what was done.
	"""
	from django.conf import settings
	from .remoteupdate import RemoteUpdateError, update_extract_from_records, update_extract_from_remote
	extract = get_db_extract(extract_id)
	if extract is None:
		raise RuntimeError("Extract not found")
	if extract["update_url"]:
		applied = update_extract_from_remote(get_pgmap(), extract, fetch)
		if applied == 0:
			return "already up to date with {}".format(extract["update_url"])
		return "updated from {} ({} activity row{})".format(extract["update_url"], applied,
			"" if applied == 1 else "s")
	if extract["up_to_date"]:
		return "already up to date"
	reason = ""
	if extract["query_mode"] == "membership" and getattr(settings, "EXTRACT_UPDATE_FROM_RECORDS", True):
		try:
			applied = update_extract_from_records(get_pgmap(), extract)
			return "updated ({} activity row{})".format(applied, "" if applied == 1 else "s")
		except RemoteUpdateError as err:
			# Whatever was applied before the failure is replaced along with the rest
			reason = " afresh from the map ({})".format(err)
	change_db_extract("UpdateExtract", extract_id)
	return "updated" + reason

def find_db_extract(extract_id=None, name=None):
	"""The one extract with the ID, or the name, given; ValueError if there is not exactly one."""
	if (extract_id is None) == (not name):
		raise ValueError("Give an extract's ID or its name")
	extracts = list_db_extracts()
	if extract_id is not None:
		chosen = [extract for extract in extracts if extract["id"] == extract_id]
		if not chosen:
			raise ValueError("No extract with ID {}".format(extract_id))
	else:
		chosen = [extract for extract in extracts if extract["name"] == name]
		if not chosen:
			raise ValueError("No extract named {}".format(name))
		if len(chosen) > 1:
			raise ValueError("More than one extract is named {}; give its ID".format(name))
	return chosen[0]

def export_db_extract(extract_id, path, edit_ids=True):
	"""Write an extract to a file, its format chosen by the name, as a download writes it."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		return t.ExportExtractToFile(extract_id, "", path, edit_ids)
	finally:
		t.Abort() # Read only; nothing to commit.

def import_db_extract(path, name, bbox=None, wkt=None, edit_ids=(-1, -1), buffer=0):
	"""Store a map file as a new extract, its boundary wkt grown outwards by
	buffer metres if both are given. Returns (ID, nodes, ways, relations)."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		if wkt and buffer:
			wkt = t.BufferExtractBoundary(wkt, float(buffer))
		found = list(t.ImportExtractFromFile(path, name, pgmap.vectord(bbox or []), wkt or "", *edit_ids))
		t.Commit()
		return tuple(found)
	except BaseException:
		t.Abort()
		raise

def compare_db_extracts(extract_id=None):
	"""Compare one extract, or with None every extract, with a fresh map query of its
	area, all in one snapshot. Returns a list of pgmap.ExtractComparison."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		if extract_id is None:
			results = pgmap.vectorextractcomparison()
			t.CompareAllExtracts(results)
			return list(results)
		result = pgmap.ExtractComparison()
		t.CompareExtract(extract_id, "", result)
		return [result]
	finally:
		t.Abort() # Read only; nothing to commit.

def change_db_extract(method, extract_id):
	"""Run PgTransaction.UpdateExtract or DeleteExtract on one extract and commit."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		getattr(t, method)(extract_id, "")
		t.Commit()
	except BaseException:
		t.Abort()
		raise
