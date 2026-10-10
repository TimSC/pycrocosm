"""Brings a stored extract up to date from another server's published edits.

The other server is asked, over its web API, for the edit activity recorded
since the extract's checkpoint. Each record carries the objects an edit
changed and the ones around them, and those are applied to the extract with
pgmap's PgExtractPatch. The map database of this server is not consulted: the
other server's map is the one the extract belongs to.

A record carries everything an update needs, so nothing else is asked of the
other server. The exception is a record made before the relations holding an
edit's related nodes were kept ("relatedparents"): for edits that old, the
other server's ordinary API is asked which relations hold each node or way
newly brought into the extract.
"""
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from django.conf import settings

import pgmap
from pycrocosm import common

# Activity rows asked for at a time, and IDs in one request for several nodes
ACTIVITY_WINDOW = 50
NODES_PER_REQUEST = 200
# Times to go back for what applying the last answers showed to be needed
FOLLOW_UP_ROUNDS = 5

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

	def activities(self, first_id, last_id):
		"""The activity rows with IDs in the range, in order, as elements."""
		rows = []
		while first_id <= last_id:
			root = self.get_xml("replication/edit_activities?first_id={}&last_id={}".format(first_id, last_id))
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

	def relations_of(self, kind, obj_id):
		"""The relations that have a node or way as a member, as a document's elements."""
		return list(self.get_xml("api/0.6/{}/{}/relations".format(kind, obj_id)))

	def nodes(self, ids):
		found = []
		ids = sorted(ids)
		for start in range(0, len(ids), NODES_PER_REQUEST):
			chunk = ",".join(str(i) for i in ids[start:start + NODES_PER_REQUEST])
			found.extend(self.get_xml("api/0.6/nodes?nodes=" + chunk))
		return found

def is_object(element):
	return element.tag in ("node", "way", "relation")

def send_objects(patch, elements):
	"""Pass object elements, as the API wrote them, to a pgmap handler."""
	elements = [e for e in elements if is_object(e)]
	if len(elements) == 0:
		return
	document = '<osm version="0.6">' + "".join(ET.tostring(e, encoding="unicode") for e in elements) + "</osm>"
	pgmap.LoadFromOsmXml(document, patch)

def check_groups(rows, checkpoint_atomic):
	"""Refuse activity that cannot be applied a whole edit at a time, as a local update does."""
	groups = {}
	for row in rows:
		atomic = row.get("atomic_edit_id")
		index = row.get("block_index")
		if atomic is None or index is None or int(atomic) <= checkpoint_atomic:
			raise RemoteUpdateError("Ungrouped activity at row {}; extract update aborted".format(row.get("id")))
		groups.setdefault(int(atomic), []).append(int(index))
	for indexes in groups.values():
		if sorted(indexes) != list(range(len(indexes))):
			raise RemoteUpdateError("Incomplete atomic edit in pending activity")

class RemoteExtractUpdate:
	def __init__(self, pgmap_object, extract_id, server):
		self.map = pgmap_object
		self.extract_id = extract_id
		self.server = server

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

	@staticmethod
	def follow_ups(patch, created):
		"""What applying a patch showed is still needed from the other server."""
		arrived = [(kind, obj_id) for kind in ("node", "way") for obj_id in patch.GetArrivedIds(kind)
			if (kind, obj_id) not in created]
		return arrived, list(patch.GetMissingNodeIds())

	def apply_rows(self, rows, checkpoint):
		"""Apply whole edits, then set the extract's checkpoint to the last of them."""
		created = set()
		# Whether every record says which relations hold its related nodes.
		# Records from before that was kept do not, and what they leave out
		# has to be asked for.
		complete = all(row.find("relatedparents") is not None and
			row.find("relatedparents").get("null") != "true" for row in rows)

		def first(patch):
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
					if action == "create":
						created.update((e.tag, int(e.get("id"))) for e in updated)
				for section in ("affectedparents", "related", "relatedparents"):
					holder = row.find(section)
					if holder is not None:
						send_objects(patch, holder)
			patch.Apply()
			needed = self.follow_ups(patch, created)
			if complete:
				# The records said which relations hold what has arrived
				needed = ([], needed[1])
			if not needed[0] and not needed[1]:
				patch.SetCheckpoint(*checkpoint)
			return needed

		arrived, missing = self.patching(first)
		rounds = 0
		while arrived or missing:
			rounds += 1
			if rounds > FOLLOW_UP_ROUNDS:
				raise RemoteUpdateError("The other server did not supply everything the extract needs"
					+ (": nodes {}".format(", ".join(str(i) for i in missing[:10])) if missing else ""))
			# Asked for outside any transaction, so nothing here is locked meanwhile
			fetched = list(self.server.nodes(missing)) if missing else []
			for kind, obj_id in arrived:
				fetched.extend(self.server.relations_of(kind, obj_id))

			def again(patch):
				send_objects(patch, fetched)
				patch.Apply()
				needed = self.follow_ups(patch, created)
				if complete:
					needed = ([], needed[1])
				if not needed[0] and not needed[1]:
					patch.SetCheckpoint(*checkpoint)
				return needed
			arrived, missing = self.patching(again)

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
			rows = held + self.server.activities(position, last)
			position = last + 1
			check_groups(rows, atomic_done)
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
		return applied

def update_extract_from_remote(pgmap_object, extract, fetch=None):
	"""Update one extract, described as replicate.extracts gives it, from its update_url.

	Returns the number of activity rows applied; zero means it was up to date.
	"""
	if extract["edit_activity_id"] is None or extract["atomic_edit_id"] is None:
		raise RemoteUpdateError("Extract has no established synchronization checkpoint")
	server = RemoteServer(extract["update_url"], fetch)
	return RemoteExtractUpdate(pgmap_object, extract["id"], server).run(
		(extract["edit_activity_id"], extract["atomic_edit_id"]))
