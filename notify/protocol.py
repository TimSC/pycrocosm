"""The edit notification protocol: msgpack-RPC over a plain TCP connection.

Messages are msgpack arrays, one after another on the connection, as
msgpack-RPC (https://github.com/msgpack-rpc/msgpack-rpc/blob/master/spec.md)
defines them:

	request       [0, msgid, method, params]    client to server
	response      [1, msgid, error, result]     server to client
	notification  [2, method, params]           server to client

params is an array. error is nil on success, or else a map with an integer
"code" and a "message"; result is nil on error.

Requests (the client sends hello first; anything else before it is refused):

	hello([{"protocol": 2, "client": "name"}])
		-> {"protocol": 2, "server": "...", "edit_activity_id": N, "atomic_edit_id": M}
	subscribe_edits([]), subscribe_edits([{"bbox": [west, south, east, north]}])
	or subscribe_edits([{"wkt": "POLYGON((...))"}])
		-> {"edit_activity_id": N, "atomic_edit_id": M}
		Asks to be told of new edits, with "edits" notifications: all of
		them, or those that may concern the area. The IDs are the map's
		latest, read from it when subscribing: edits after them will be
		announced, and none up to them, which the replication API gives.
	subscribe_records([]), subscribe_records([{"bbox": [...]}])
	or subscribe_records([{"wkt": "..."}]), each optionally with
	"format": "xml" (the default) or "msgpack"
		-> {"edit_activity_id": N, "atomic_edit_id": M}
		Asks for the edit activity records themselves, as they are made, with
		"records" notifications: all of them, or those that may concern the
		area.
	unsubscribe_edits([]), unsubscribe_records([]) -> true
	ping([]) -> "pong"

A connection may hold one subscription of each kind at a time: subscribing
again needs unsubscribing first. An area is a bbox, or WKT: a valid polygon or
multipolygon, no wider than 180 degrees of longitude. Edits concern a bbox as
the replication API's bbox filter decides; for WKT, the box of one of an
edit's objects must also meet the shape itself.

Notifications from the server:

	edits([{"edit_activity_id": N, "atomic_edit_id": M}])
		There are new edits, up to these IDs, that may concern the
		subscription. Fetch them with the replication API
		(replication/edit_activities?first_id=...). A notification is a hint,
		not the edits themselves, and several edits may come as one; after
		missing any, a client catches up from replication/edit_ids.
	records([{"first_id": A, "last_id": B, "records": n,
			"edit_activity_id": N, "atomic_edit_id": M, "xml_gz": <bin>}])
		The edit activity records with IDs from A to B that may concern the
		subscription, n of them, as a gzipped XML document in the form
		replication/edit_activities gives. The ranges of one notification
		and the next follow on from each other, except where a range held
		nothing concerning the area, which is not sent.
	records([{"first_id": A, "last_id": B, "records": n,
			"edit_activity_id": N, "atomic_edit_id": M, "editactivities": [...]}])
		The same, for a subscription in the msgpack format: the records as
		maps, as replication/edit_activities?format=msgpack.gz gives them, with
		their objects o5m encoded.
	heartbeat([{"edit_activity_id": N, "atomic_edit_id": M}])
		Sent to subscribers now and then, with where the map has got to, so a
		client can tell the connection is alive.

Only what clients send is limited, in size and how often (see server.Limits);
what the server sends is not. A client must keep up with it, though: one that
leaves too much unread for too long is dropped.
"""

import math

import msgpack

PROTOCOL_VERSION = 2

REQUEST, RESPONSE, NOTIFICATION = 0, 1, 2

# Error codes, after JSON-RPC's
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
NOT_READY = -32000 #hello has not been sent
UNSUPPORTED_PROTOCOL = -32001
TOO_MANY_REQUESTS = -32002
ALREADY_SUBSCRIBED = -32003
NOT_SUBSCRIBED = -32004

class ProtocolError(Exception):
	"""A request the server answers with an error."""

	def __init__(self, code, message):
		Exception.__init__(self, message)
		self.code = code
		self.message = message

	def as_error(self):
		return {"code": self.code, "message": self.message}

def pack(message):
	return msgpack.packb(message, use_bin_type=True)

def response(msgid, result=None, error=None):
	return pack([RESPONSE, msgid, error, None if error is not None else result])

def notification(method, params):
	return pack([NOTIFICATION, method, params])

def unpacker(max_message_bytes):
	"""Splits a stream into messages. Refuses (with msgpack's BufferFull) one
	larger than max_message_bytes, so a client cannot make the server hold
	an endless message."""
	return msgpack.Unpacker(raw=False, max_buffer_size=max_message_bytes, strict_map_key=True)

def parse_request(message):
	"""A request's (msgid, method, params). Raises ProtocolError for anything
	else, with the msgid if one could be read."""
	if not isinstance(message, (list, tuple)) or len(message) != 4 or message[0] != REQUEST:
		raise ProtocolError(INVALID_REQUEST, "Expected a request: [0, msgid, method, params]")
	_, msgid, method, params = message
	if not isinstance(msgid, int) or isinstance(msgid, bool) or not 0 <= msgid < 2 ** 32:
		raise ProtocolError(INVALID_REQUEST, "The msgid must be an integer from 0 to 2**32-1")
	if not isinstance(method, str):
		raise ProtocolError(INVALID_REQUEST, "The method must be a string")
	if not isinstance(params, (list, tuple)):
		raise ProtocolError(INVALID_PARAMS, "The params must be an array")
	return msgid, method, list(params)

def options(params, allowed):
	"""The map a method takes as its one optional parameter, checked to hold only
	the allowed keys."""
	if len(params) == 0:
		return {}
	if len(params) != 1 or not isinstance(params[0], dict):
		raise ProtocolError(INVALID_PARAMS, "Expected at most one parameter, a map")
	unknown = sorted(str(key) for key in params[0] if key not in allowed)
	if unknown:
		raise ProtocolError(INVALID_PARAMS, "Unknown option: {}".format(", ".join(unknown)))
	return params[0]

def parse_bbox(value):
	"""A subscription's area as (west, south, east, north), checked."""
	if not isinstance(value, (list, tuple)) or len(value) != 4 or \
			not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
		raise ProtocolError(INVALID_PARAMS, "bbox must be four numbers: [west, south, east, north]")
	west, south, east, north = (float(v) for v in value)
	if not all(math.isfinite(v) for v in (west, south, east, north)):
		raise ProtocolError(INVALID_PARAMS, "bbox must be finite numbers")
	if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
		raise ProtocolError(INVALID_PARAMS, "bbox must have west <= east and south <= north, within "
			"longitude -180 to 180 and latitude -90 to 90")
	return (west, south, east, north)

# The formats a records subscription may ask for
RECORD_FORMATS = ("xml", "msgpack")

def parse_format(given):
	"""The format of a records subscription's options, "xml" unless it says."""
	file_format = given.get("format", "xml")
	if file_format not in RECORD_FORMATS:
		raise ProtocolError(INVALID_PARAMS, "format must be one of: {}".format(", ".join(RECORD_FORMATS)))
	return file_format

def edit_ids(ids):
	return {"edit_activity_id": ids[0], "atomic_edit_id": ids[1]}

def parse_area(given):
	"""The area of a subscription's options: None (everywhere), ("bbox", (west,
	south, east, north)) or ("wkt", text), the WKT not yet checked as a shape."""
	bbox, wkt = given.get("bbox"), given.get("wkt")
	if bbox is not None and wkt is not None:
		raise ProtocolError(INVALID_PARAMS, "Give a bbox or wkt, not both")
	if bbox is not None:
		return ("bbox", parse_bbox(bbox))
	if wkt is not None:
		if not isinstance(wkt, str) or not wkt.strip():
			raise ProtocolError(INVALID_PARAMS, "wkt must be the text of a polygon or multipolygon")
		return ("wkt", wkt.strip())
	return None
