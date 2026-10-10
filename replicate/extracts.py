"""Access to the extracts stored in the map database, through pgmap."""

import datetime

import pgmap

from pycrocosm.mapdb import get_pgmap

def describe_db_extract(info):
	extract = {
		"id": info.extractId,
		"name": info.name,
		"bbox": list(info.bbox),
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

def save_db_extract(bbox, name):
	"""Store a snapshot of the map within bbox and return the new extract's ID."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		extract_id = t.SaveExtract(bbox, name)
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

	Returns a short description of what was done.
	"""
	from .remoteupdate import update_extract_from_remote
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
	change_db_extract("UpdateExtract", extract_id)
	return "updated"

def change_db_extract(method, extract_id):
	"""Run PgTransaction.UpdateExtract or DeleteExtract on one extract and commit."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		getattr(t, method)(extract_id, "")
		t.Commit()
	except BaseException:
		t.Abort()
		raise
