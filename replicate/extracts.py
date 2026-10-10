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
		"up_to_date": not info.pendingActivity,
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

def change_db_extract(method, extract_id):
	"""Run PgTransaction.UpdateExtract or DeleteExtract on one extract and commit."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		getattr(t, method)(extract_id, "")
		t.Commit()
	except BaseException:
		t.Abort()
		raise
