"""Brings a stored extract up to date from another server's published edits.

The other server is asked, over its web API, for the edit activity recorded
since the extract's checkpoint. Each record carries the objects an edit
changed and the ones around them, and those are applied to the extract with
pgmap's PgExtractPatch. The map database of this server is not consulted: the
other server's map is the one the extract belongs to.

A record carries everything an update needs, so nothing else is asked of the
other server. Records made before the relations holding an edit's related
nodes were kept ("relatedparents") do not, and an update that meets one is
refused: the extract has to be saved or imported afresh from a point after
them.

Only the records that may concern the extract's rectangle are asked for, with
the edit activity API's bbox filter, so a small extract of a busy map does not
read every edit made anywhere. What the filter leaves out could not have
changed the extract: an object is in it by having a node in the rectangle, or
by holding or belonging to one that has, and every record that touches such an
object reaches into the rectangle before or after its edit. Setting
EXTRACT_UPDATE_USE_BBOX to False asks for every record instead; the extract
comes out the same.
"""
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from django.conf import settings

import pgmap
from pycrocosm import common

# The range of activity row IDs asked about at a time. With the filter by
# area only some of them come back, and applying them costs what they touch,
# not what the extract holds, so the range can be wide.
ACTIVITY_WINDOW = 2000
# Rows read from this map's own table in one query
LOCAL_ROWS_PER_QUERY = 100

class RemoteUpdateError(Exception):
	"""The extract could not be updated from the other server."""

def http_fetch(url):
	"""The body of a web address, or RemoteUpdateError."""
	timeout = getattr(settings, "EXTRACT_UPDATE_TIMEOUT", 120)
	maximum = getattr(settings, "EXTRACT_UPDATE_MAXIMUM_BYTES", 500 * 1024 * 1024)
	request = urllib.request.Request(url, headers={"User-Agent": "pycrocosm extract update"})
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			body = response.read(maximum + 1)
	except urllib.error.HTTPError as err:
		raise RemoteUpdateError("{} answered {}".format(url, err.code))
	except (urllib.error.URLError, OSError, ValueError) as err:
		raise RemoteUpdateError("{} could not be read: {}".format(url, err))
	if len(body) > maximum:
		raise RemoteUpdateError("{} sent more than {} bytes".format(url, maximum))
	return body

class RemoteServer:
	"""The parts of another pycrocosm server's web API that an update uses."""
	def __init__(self, base_url, fetch=None):
		self.base = base_url if base_url.endswith("/") else base_url + "/"
		self.fetch = fetch or http_fetch

	def get_xml(self, path):
		url = self.base + path
		try:
			return fromstring(self.fetch(url))
		except RemoteUpdateError:
			raise
		except Exception as err:
			raise RemoteUpdateError("{} did not send a document that can be read: {}".format(url, err))

	def latest_edit_ids(self):
		root = self.get_xml("replication/edit_ids")
		try:
			return int(root.attrib["edit_activity_id"]), int(root.attrib["atomic_edit_id"])
		except (KeyError, ValueError):
			raise RemoteUpdateError("{}replication/edit_ids did not give the server's edit IDs".format(self.base))

	def activities(self, first_id, last_id, bbox=None):
		"""The activity rows with IDs in the range, in order, as elements. With
		bbox (min lon, min lat, max lon, max lat), only those that may concern it."""
		area = "&bbox=" + ",".join(repr(float(v)) for v in bbox) if bbox else ""
		rows = []
		while first_id <= last_id:
			root = self.get_xml("replication/edit_activities?first_id={}&last_id={}{}".format(first_id, last_id, area))
			rows.extend(root.findall("editactivity"))
			if root.get("truncated") != "true":
				break
			# The server sends only so many records at a time, and says where to go on from
			try:
				following = int(root.get("next_id"))
			except (TypeError, ValueError):
				following = 0
			if following <= first_id:
				raise RemoteUpdateError("{}replication/edit_activities cut its answer short without "
					"saying where to continue".format(self.base))
			first_id = following
		return rows

class LocalServer:
	"""This map's own edit activity, read straight from its table.

	It answers what RemoteServer asks another server over the web, with the
	same records in the same form, so an extract of this map is brought up to
	date by the edits that concern it, not by being taken from the map afresh.
	"""
	def __init__(self, pgmap_object):
		self.map = pgmap_object

	def latest_edit_ids(self):
		t = self.map.GetTransaction("ACCESS SHARE")
		try:
			latest = t.GetLatestEditIds()
			return int(latest[0]), int(latest[1])
		finally:
			common.abort_transaction(t)

	def activities(self, first_id, last_id, bbox=None):
		from . import views # Which writes the records; imported late, as it imports this module's users
		rows = []
		area = pgmap.vectord([float(v) for v in bbox] if bbox else [])
		while first_id <= last_id:
			t = self.map.GetTransaction("ACCESS SHARE")
			try:
				results = pgmap.vectorsharedptreditactivity()
				error = pgmap.PgMapError()
				t.QueryEditActivityByIds(first_id, last_id, 0, results, error, LOCAL_ROWS_PER_QUERY + 1, area, False)
				if error.errStr:
					raise RemoteUpdateError("The edit activity could not be read: " + error.errStr)
				sent = min(results.size(), LOCAL_ROWS_PER_QUERY)
				activities = [results[i] for i in range(sent)]
				lists = [views.activity_object_lists(activity) for activity in activities]
				fragments = views.fetch_activity_objects(lists, t)
				document = ["<editactivities>"]
				for activity, recordLists in zip(activities, lists):
					document.extend(views.edit_activity_xml(activity, recordLists, fragments))
				document.append("</editactivities>")
				more = results.size() > LOCAL_ROWS_PER_QUERY
				following = activities[-1].objId + 1 if activities else last_id + 1
			finally:
				common.abort_transaction(t)
			rows.extend(ET.fromstring("".join(document)).findall("editactivity"))
			if not more:
				break
			first_id = following
		return rows

def is_object(element):
	return element.tag in ("node", "way", "relation")

def send_objects(patch, elements):
	"""Pass object elements, as the API wrote them, to a pgmap handler."""
	elements = [e for e in elements if is_object(e)]
	if len(elements) == 0:
		return
	document = '<osm version="0.6">' + "".join(ET.tostring(e, encoding="unicode") for e in elements) + "</osm>"
	pgmap.LoadFromOsmXml(document, patch)

def check_groups(rows, checkpoint_atomic, filtered=False):
	"""Refuse activity that cannot be applied a whole edit at a time, as a local update does.

	When the rows were chosen by area, the blocks of an edit that do not
	concern the area are absent, so the blocks seen need not run from zero
	without a gap; no block may come twice."""
	groups = {}
	for row in rows:
		atomic = row.get("atomic_edit_id")
		index = row.get("block_index")
		if atomic is None or index is None or int(atomic) <= checkpoint_atomic:
			raise RemoteUpdateError("Ungrouped activity at row {}; extract update aborted".format(row.get("id")))
		groups.setdefault(int(atomic), []).append(int(index))
	for indexes in groups.values():
		if len(set(indexes)) != len(indexes) or (not filtered and sorted(indexes) != list(range(len(indexes)))):
			raise RemoteUpdateError("Incomplete atomic edit in pending activity")

class RemoteExtractUpdate:
	def __init__(self, pgmap_object, extract_id, server, bbox=None):
		self.map = pgmap_object
		self.extract_id = extract_id
		self.server = server
		self.bbox = bbox # The area to ask for records of; None for all records

	def patching(self, work):
		"""Run work(patch) in a transaction of its own, committing what it did."""
		t = self.map.GetTransaction("ACCESS SHARE")
		try:
			patch = t.StartPatchExtract(self.extract_id)
			result = work(patch)
			del patch
			t.Commit()
			return result
		except BaseException:
			common.abort_transaction(t)
			raise

	def apply_rows(self, rows, checkpoint):
		"""Apply whole edits, then set the extract's checkpoint to the last of them."""
		for row in rows:
			holder = row.find("relatedparents")
			if holder is None or holder.get("null") == "true":
				raise RemoteUpdateError("Activity row {} was recorded before edits said which relations hold "
					"their related nodes, and cannot be applied; save or import the extract afresh".format(row.get("id")))

		def work(patch):
			for row in rows:
				action = row.get("action")
				updated = [e for e in row.find("updated") if is_object(e)] if row.find("updated") is not None else []
				if action == "delete":
					# The record of what was there says what has gone, whether
					# or not the deleted versions themselves could be sent
					existing = row.find("existing")
					for element in (existing if existing is not None else []):
						if is_object(element):
							patch.RemoveObject(element.tag, int(element.get("id")), int(element.get("version")) + 1)
					for element in updated:
						patch.RemoveObject(element.tag, int(element.get("id")), int(element.get("version")))
				else:
					send_objects(patch, updated)
				for section in ("affectedparents", "related", "relatedparents"):
					holder = row.find(section)
					if holder is not None:
						send_objects(patch, holder)
			patch.Apply()
			missing = list(patch.GetMissingNodeIds())
			if missing:
				raise RemoteUpdateError("The other server did not supply everything the extract needs: nodes {}".format(
					", ".join(str(i) for i in missing[:10])))
			patch.SetCheckpoint(*checkpoint)

		self.patching(work)

	def run(self, checkpoint):
		"""Update from the checkpoint (edit activity ID, atomic edit ID). Returns
		the number of activity rows applied."""
		latest_activity, latest_atomic = self.server.latest_edit_ids()
		if latest_activity < checkpoint[0] or latest_atomic < checkpoint[1]:
			raise RemoteUpdateError("Source activity precedes extract checkpoint; source may have been reset")
		applied = 0
		position = checkpoint[0] + 1
		atomic_done = checkpoint[1]
		held = [] # Rows of an edit whose last row has not been seen yet
		while position <= latest_activity:
			last = min(position + ACTIVITY_WINDOW - 1, latest_activity)
			rows = held + self.server.activities(position, last, self.bbox)
			position = last + 1
			check_groups(rows, atomic_done, self.bbox is not None)
			if position <= latest_activity and len(rows) > 0:
				# The final edit in the window may continue in the next one
				final = rows[-1].get("atomic_edit_id")
				held = [row for row in rows if row.get("atomic_edit_id") == final]
				rows = rows[:len(rows) - len(held)]
			else:
				held = []
			if len(rows) == 0:
				continue
			checkpoint = (int(rows[-1].get("id")), int(rows[-1].get("atomic_edit_id")))
			self.apply_rows(rows, checkpoint)
			atomic_done = checkpoint[1]
			applied += len(rows)
		if checkpoint[0] < latest_activity:
			# The records after the last one applied did not concern the
			# extract. It has still caught up with them, and says so, or the
			# same records would be asked about on every update.
			self.patching(lambda patch: patch.SetCheckpoint(latest_activity, latest_atomic))
		return applied

def update_extract_from_records(pgmap_object, extract):
	"""Update one extract of this map from the map's own edit activity.

	The work is in proportion to the edits that concern the extract, where
	taking it from the map afresh is in proportion to its size. It follows the
	rule of a map without stored bounding boxes, as an update from another
	server does. Returns the number of activity rows applied.
	"""
	if extract["edit_activity_id"] is None or extract["atomic_edit_id"] is None:
		raise RemoteUpdateError("Extract has no established synchronization checkpoint")
	bbox = None
	if getattr(settings, "EXTRACT_UPDATE_USE_BBOX", True) and len(extract.get("bbox") or []) == 4:
		bbox = list(extract["bbox"])
	return RemoteExtractUpdate(pgmap_object, extract["id"], LocalServer(pgmap_object), bbox).run(
		(extract["edit_activity_id"], extract["atomic_edit_id"]))

def update_extract_from_remote(pgmap_object, extract, fetch=None):
	"""Update one extract, described as replicate.extracts gives it, from its update_url.

	Returns the number of activity rows applied; zero means it was up to date.
	"""
	if extract["edit_activity_id"] is None or extract["atomic_edit_id"] is None:
		raise RemoteUpdateError("Extract has no established synchronization checkpoint")
	server = RemoteServer(extract["update_url"], fetch)
	bbox = None
	if getattr(settings, "EXTRACT_UPDATE_USE_BBOX", True) and len(extract.get("bbox") or []) == 4:
		bbox = list(extract["bbox"])
	return RemoteExtractUpdate(pgmap_object, extract["id"], server, bbox).run(
		(extract["edit_activity_id"], extract["atomic_edit_id"]))
