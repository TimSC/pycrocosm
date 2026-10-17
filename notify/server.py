"""A TCP server that tells subscribed clients when the map has new edits.

PostgreSQL announces each edit with NOTIFY (see DbSetEditNotifications in
pgmap's dbadmin.cpp). The server LISTENs for that and works out which
subscribers the new edits may concern. Each connection may hold two
subscriptions, each at most once: to "edits" notifications, which say how far
the map has got, and to the "records" themselves, the edit activity records
as the replication API gives them. Each may be limited to an area, a bbox or a
WKT polygon or multipolygon (see protocol.py). The server also asks the map
for its latest edit IDs now and then, so nothing is missed while its database
connection is being remade.

What clients send is limited, in size and in how often. What the server sends
them is not limited, but a client must keep up with it: one that falls behind,
leaving more than a high-water mark unread for longer than a time limit, is
dropped, and catches up through the replication API when it connects again.

Everything runs in one asyncio loop. Calls into the map database, which block,
run one at a time in a thread of their own.
"""

import asyncio
import collections
import concurrent.futures
import dataclasses
import gzip
import socket
import time

import msgpack

from . import protocol
from .protocol import ProtocolError

@dataclasses.dataclass
class Limits:
	"""What the server allows. Most limit what clients send; what the server
	sends them is not limited, but a client that falls behind with it is dropped."""
	max_connections: int = 64
	max_per_address: int = 10 #Connections from one address at a time
	max_message_bytes: int = 64 * 1024 #The largest request a client may send, WKT and all
	requests_per_second: float = 5 #Requests each connection may make, on average...
	request_burst: int = 20 #...and at once
	hello_timeout: float = 10 #Seconds a new connection has to say hello
	subscribe_timeout: float = 60 #Seconds it then has to subscribe to something
	write_high_water: int = 1024 * 1024 #Bytes unread by a client before it counts as behind...
	behind_timeout: float = 60 #...and seconds it may stay behind before it is dropped
	keepalive: int = 60 #Seconds a connection may be idle before TCP checks the client is still there
	heartbeat: float = 30 #Seconds between heartbeats, and checks for missed edits

class EditSource:
	"""Where the server learns about edits. Its methods block; the server calls
	them in a thread of their own. An area is None for everywhere,
	("bbox", (west, south, east, north)) or ("wkt", text)."""

	def latest(self):
		"""The latest (edit activity ID, atomic edit ID)."""
		raise NotImplementedError

	def check_wkt(self, wkt):
		"""The WKT of a valid polygon or multipolygon, tidied; ValueError, saying
		why, for anything else."""
		raise NotImplementedError

	def concerns(self, first_id, last_id, area):
		"""Whether any edit activity record with an ID in the range may concern the area."""
		raise NotImplementedError

	def records(self, first_id, last_id, area, file_format="xml"):
		"""The edit activity records with IDs in the range that may concern the
		area, as (how many, the records), or None if there are none. The
		records are the replication API's XML document of them as UTF-8, or
		with file_format "msgpack", the list of records its msgpack document
		holds, as maps, their objects o5m encoded."""
		raise NotImplementedError

class PgmapEditSource(EditSource):
	"""The map's own edit activity, read through pgmap. make_pgmap makes the
	connection, in the thread that uses it."""

	def __init__(self, make_pgmap):
		self.make_pgmap = make_pgmap
		self.map = None

	def _transaction(self):
		if self.map is None or not self.map.Ready():
			self.map = self.make_pgmap()
		return self.map.GetTransaction("ACCESS SHARE")

	def latest(self):
		t = self._transaction()
		try:
			ids = t.GetLatestEditIds()
			return (int(ids[0]), int(ids[1]))
		finally:
			t.Abort()

	def check_wkt(self, wkt):
		t = self._transaction()
		try:
			#Checked as an extract's boundary is: a valid polygon or multipolygon,
			#in range and not crossing the antimeridian
			return t.BufferExtractBoundary(wkt, 0)
		except RuntimeError as err:
			raise ValueError(str(err).replace("Standard exception: ", "").replace("Standard runtime exception: ", ""))
		finally:
			t.Abort()

	def _query(self, t, first_id, last_id, area, limit):
		import pgmap
		results = pgmap.vectorsharedptreditactivity()
		error = pgmap.PgMapError()
		bbox = list(area[1]) if area and area[0] == "bbox" else []
		wkt = area[1] if area and area[0] == "wkt" else ""
		#The same filter as replication/edit_activities?bbox=, and for a WKT
		#area, the shape itself
		t.QueryEditActivityByIds(first_id, last_id, 0, results, error, limit, pgmap.vectord(bbox), False, wkt)
		if error.errStr:
			raise RuntimeError(error.errStr)
		return results

	def concerns(self, first_id, last_id, area):
		t = self._transaction()
		try:
			return self._query(t, first_id, last_id, area, 1).size() > 0
		finally:
			t.Abort()

	def records(self, first_id, last_id, area, file_format="xml"):
		from replicate import views #Which writes the records as the API does
		t = self._transaction()
		try:
			results = self._query(t, first_id, last_id, area, 0)
			activities = [results[i] for i in range(results.size())]
			if not activities:
				return None
			if file_format == "msgpack":
				return len(activities), views.edit_activity_records(activities, t)
			return len(activities), views.edit_activities_document(activities, t)
		finally:
			t.Abort()

class PgListener:
	"""LISTENs on a PostgreSQL channel, calling on_notify for each notification,
	and on_connect each time the connection is made, since notifications sent
	while it was not there are lost. Remakes the connection if it fails."""

	def __init__(self, connection_string, channel, on_notify, on_connect, log):
		self.connection_string = connection_string
		self.channel = channel
		self.on_notify = on_notify
		self.on_connect = on_connect
		self.log = log
		self.connection = None
		self.closed = False

	async def start(self):
		await self._connect()

	async def _connect(self):
		import psycopg2
		from psycopg2 import sql
		loop = asyncio.get_running_loop()
		delay = 1
		while not self.closed:
			try:
				connection = await loop.run_in_executor(None, psycopg2.connect, self.connection_string)
				connection.autocommit = True #LISTEN takes effect at once, and notifications are not held
				with connection.cursor() as cursor:
					cursor.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.channel)))
				self.connection = connection
				loop.add_reader(connection.fileno(), self._readable)
				self.log("Listening for edits on {}".format(self.channel))
				self.on_connect()
				return
			except psycopg2.Error as err:
				self.log("Cannot listen for edits ({}); trying again in {}s".format(str(err).strip(), delay))
				await asyncio.sleep(delay)
				delay = min(delay * 2, 30)

	def _readable(self):
		import psycopg2
		try:
			self.connection.poll()
		except psycopg2.Error as err:
			self.log("Lost the database connection: {}".format(str(err).strip()))
			self._drop()
			asyncio.get_running_loop().create_task(self._connect())
			return
		while self.connection.notifies:
			self.on_notify(self.connection.notifies.pop(0).payload)

	def _drop(self):
		if self.connection is not None:
			try:
				asyncio.get_running_loop().remove_reader(self.connection.fileno())
			except (ValueError, OSError):
				pass
			try:
				self.connection.close()
			except Exception:
				pass
			self.connection = None

	def close(self):
		self.closed = True
		self._drop()

def set_keepalive(sock, idle):
	"""Has TCP check that an idle connection's peer is still there: after idle
	seconds, then every ten, giving up after six tries, so that a client that
	has gone without a word is noticed in about idle + 60 seconds. Where the
	system does not let these be set, its own defaults (often two hours) apply."""
	if sock is None:
		return
	sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
	for name, value in (("TCP_KEEPIDLE", idle), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 6)):
		if hasattr(socket, name):
			sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), max(1, int(value)))

class Connection(asyncio.Protocol):
	"""One client."""

	def __init__(self, notifier):
		self.notifier = notifier
		self.limits = notifier.limits
		self.transport = None
		self.address = None
		self.greeted = False
		#kind: what is subscribed to, for those subscribed to: for "edits" the
		#area, for "records" (area, format)
		self.subscriptions = {}
		self.unpacker = protocol.unpacker(self.limits.max_message_bytes)
		self.tokens = float(self.limits.request_burst)
		self.refilled = time.monotonic()
		self.deadline = None
		self.requests = collections.deque() #Waiting to be handled, in order
		self.worker = None
		self.behind = None #The timer for dropping it, while it is behind

	# The connection

	def connection_made(self, transport):
		self.transport = transport
		peer = transport.get_extra_info("peername")
		self.address = peer[0] if peer else "?"
		if not self.notifier.admit(self):
			transport.abort()
			return
		set_keepalive(transport.get_extra_info("socket"), self.limits.keepalive)
		#asyncio calls pause_writing when this much is waiting to be read
		transport.set_write_buffer_limits(high=self.limits.write_high_water)
		self._set_deadline(self.limits.hello_timeout, "say hello")

	def connection_lost(self, exc):
		self._set_deadline(None)
		if self.behind is not None:
			self.behind.cancel()
		if self.worker is not None:
			self.worker.cancel()
		self.notifier.release(self)

	def _set_deadline(self, seconds, what=None):
		if self.deadline is not None:
			self.deadline.cancel()
			self.deadline = None
		if seconds is not None:
			self.deadline = asyncio.get_running_loop().call_later(seconds, self.close,
				"did not {} in time".format(what))

	def close(self, reason=None):
		if reason:
			self.notifier.log("Closing {}: {}".format(self.address, reason))
		if self.transport is not None and not self.transport.is_closing():
			self.transport.close()

	def send(self, *messages):
		"""Sends messages, however large. A client that leaves too much of them
		unread for too long is dropped (see pause_writing)."""
		if self.transport is None or self.transport.is_closing():
			return
		for message in messages:
			self.transport.write(message)

	def subscribed(self, kind):
		return kind in self.subscriptions

	# Clients that fall behind

	def pause_writing(self):
		"""More than write_high_water bytes are waiting for the client. Sending goes
		on, so a client that catches up misses nothing; one that does not within
		behind_timeout is dropped."""
		if self.behind is None:
			self.behind = asyncio.get_running_loop().call_later(self.limits.behind_timeout, self._too_slow)

	def resume_writing(self):
		if self.behind is not None:
			self.behind.cancel()
			self.behind = None

	def _too_slow(self):
		self.behind = None
		self.notifier.log("Closing {}: fell behind with what it is sent, for {}s".format(
			self.address, self.limits.behind_timeout))
		if self.transport is not None:
			self.transport.abort() #What is waiting for it is thrown away

	# Requests

	def data_received(self, data):
		try:
			self.unpacker.feed(data)
		except msgpack.BufferFull:
			self.close("sent a message larger than {} bytes".format(self.limits.max_message_bytes))
			return
		while not self.transport.is_closing():
			try:
				message = next(self.unpacker)
			except StopIteration:
				return
			except (msgpack.UnpackException, ValueError) as err:
				self.send(protocol.response(None, error=ProtocolError(protocol.PARSE_ERROR,
					"Cannot read: {}".format(err)).as_error()))
				self.close("sent what cannot be read")
				return
			if not self._allowed():
				self.send(protocol.response(self._msgid(message), error=ProtocolError(protocol.TOO_MANY_REQUESTS,
					"More than {} requests a second".format(self.limits.requests_per_second)).as_error()))
				self.close("made too many requests")
				return
			#Handled one at a time, in order, as some wait on the database
			self.requests.append(message)
			if self.worker is None:
				self.worker = asyncio.get_running_loop().create_task(self._work())

	async def _work(self):
		try:
			while self.requests and not self.transport.is_closing():
				await self._handle(self.requests.popleft())
		finally:
			self.worker = None

	def _allowed(self):
		"""Takes a token from the bucket, which refills at requests_per_second."""
		now = time.monotonic()
		self.tokens = min(float(self.limits.request_burst),
			self.tokens + (now - self.refilled) * self.limits.requests_per_second)
		self.refilled = now
		if self.tokens < 1:
			return False
		self.tokens -= 1
		return True

	@staticmethod
	def _msgid(message):
		"""A message's msgid, if it looks like a request, for answering it with an error."""
		if isinstance(message, (list, tuple)) and len(message) > 1 and isinstance(message[1], int) \
				and not isinstance(message[1], bool):
			return message[1]
		return None

	async def _handle(self, message):
		msgid = self._msgid(message)
		try:
			msgid, method, params = protocol.parse_request(message)
			handler = getattr(self, "_request_" + method, None)
			if handler is None:
				raise ProtocolError(protocol.METHOD_NOT_FOUND, "No method {}".format(method))
			if method != "hello" and not self.greeted:
				raise ProtocolError(protocol.NOT_READY, "Say hello first")
			result = handler(params)
			if asyncio.iscoroutine(result):
				result = await result
		except ProtocolError as err:
			self.send(protocol.response(msgid, error=err.as_error()))
			return
		self.send(protocol.response(msgid, result))

	def _request_hello(self, params):
		given = protocol.options(params, ("protocol", "client"))
		version = given.get("protocol", protocol.PROTOCOL_VERSION)
		if version != protocol.PROTOCOL_VERSION:
			raise ProtocolError(protocol.UNSUPPORTED_PROTOCOL,
				"This server speaks protocol {}".format(protocol.PROTOCOL_VERSION))
		if not self.greeted:
			self.greeted = True
			self._set_deadline(self.limits.subscribe_timeout, "subscribe")
		result = {"protocol": protocol.PROTOCOL_VERSION, "server": "pycrocosm editnotifier"}
		result.update(protocol.edit_ids(self.notifier.latest))
		return result

	async def _subscribe(self, kind, params):
		given = protocol.options(params, ("bbox", "wkt", "format") if kind == "records" else ("bbox", "wkt"))
		if self.subscribed(kind):
			raise ProtocolError(protocol.ALREADY_SUBSCRIBED,
				"Already subscribed to {}; unsubscribe first".format(kind))
		area = protocol.parse_area(given)
		file_format = protocol.parse_format(given) if kind == "records" else None
		if area is not None and area[0] == "wkt":
			try:
				area = ("wkt", await self.notifier.call_source(self.notifier.source.check_wkt, area[1]))
			except ValueError as err:
				raise ProtocolError(protocol.INVALID_PARAMS, "wkt: {}".format(err))
		#The IDs given are the map's latest, read now: everything up to them the
		#client fetches through the API, everything after them it is sent. So the
		#server catches up first, and the subscription starts once it has, with
		#nothing in between to announce twice or miss.
		await self.notifier.refresh()
		if self.transport.is_closing():
			raise ProtocolError(protocol.INVALID_REQUEST, "The connection is closing")
		self.subscriptions[kind] = (area, file_format) if kind == "records" else area
		self._set_deadline(None)
		return protocol.edit_ids(self.notifier.latest)

	def _unsubscribe(self, kind, params):
		protocol.options(params, ())
		if not self.subscribed(kind):
			raise ProtocolError(protocol.NOT_SUBSCRIBED, "Not subscribed to {}".format(kind))
		del self.subscriptions[kind]
		#An idle connection is not kept for ever
		if not self.subscriptions:
			self._set_deadline(self.limits.subscribe_timeout, "subscribe")
		return True

	def _request_subscribe_edits(self, params):
		return self._subscribe("edits", params)

	def _request_subscribe_records(self, params):
		return self._subscribe("records", params)

	def _request_unsubscribe_edits(self, params):
		return self._unsubscribe("edits", params)

	def _request_unsubscribe_records(self, params):
		return self._unsubscribe("records", params)

	def _request_ping(self, params):
		return "pong"

class EditNotifier:
	"""The server: its clients, and what it knows of the map's edits."""

	def __init__(self, source, limits=None, log=print):
		self.source = source
		self.limits = limits or Limits()
		self.log = log
		self.connections = set()
		self.per_address = {}
		self.latest = (0, 0)
		self.server = None
		self.listener = None
		self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="editsource")
		self._checking = False
		self._again = False
		self._idle = None #Set while no check is running
		self._tasks = set()

	async def call_source(self, method, *args):
		"""Calls a method of the edit source, or anything else that uses it, in its thread."""
		return await asyncio.get_running_loop().run_in_executor(self._executor, method, *args)

	async def start(self, host, port, connection_string=None, channel=None):
		"""Starts serving. With a connection string and channel, LISTENs for edits
		there; otherwise only the periodic check notices them."""
		self._idle = asyncio.Event()
		self._idle.set()
		self.latest = await self.call_source(self.source.latest)
		self.server = await asyncio.get_running_loop().create_server(lambda: Connection(self), host, port)
		if connection_string and channel:
			self.listener = PgListener(connection_string, channel, self._notified, self.edits_changed, self.log)
			await self.listener.start()
		self._spawn(self._heartbeat())
		self.log("Serving on {}, at edit activity {}, atomic edit {}".format(
			", ".join("{}:{}".format(*s.getsockname()[:2]) for s in self.server.sockets), *self.latest))

	def port(self):
		return self.server.sockets[0].getsockname()[1]

	async def stop(self):
		if self.listener is not None:
			self.listener.close()
		for task in list(self._tasks):
			task.cancel()
		if self.server is not None:
			self.server.close()
			await self.server.wait_closed()
		for connection in list(self.connections):
			connection.close()
		self._executor.shutdown(wait=False)

	def _spawn(self, coroutine):
		task = asyncio.get_running_loop().create_task(coroutine)
		self._tasks.add(task)
		task.add_done_callback(self._tasks.discard)
		return task

	# Connections

	def admit(self, connection):
		if len(self.connections) >= self.limits.max_connections:
			self.log("Refusing {}: {} connections already".format(connection.address, len(self.connections)))
			return False
		if self.per_address.get(connection.address, 0) >= self.limits.max_per_address:
			self.log("Refusing {}: {} connections from it already".format(connection.address, self.limits.max_per_address))
			return False
		self.connections.add(connection)
		self.per_address[connection.address] = self.per_address.get(connection.address, 0) + 1
		return True

	def release(self, connection):
		if connection in self.connections:
			self.connections.discard(connection)
			remaining = self.per_address.get(connection.address, 1) - 1
			if remaining:
				self.per_address[connection.address] = remaining
			else:
				self.per_address.pop(connection.address, None)

	# Edits

	def _notified(self, payload):
		self.edits_changed()

	def edits_changed(self):
		"""Something may have changed: check, once at a time. A change noticed
		during a check is looked at when it ends, as one."""
		if self._checking:
			self._again = True
			return
		self._start_check()

	async def refresh(self):
		"""Catches up with the map's latest edits, announcing any to subscribers,
		and returns once it has."""
		if self._checking:
			self._again = True #Any check under way may have read the IDs already
		else:
			self._start_check()
		await self._idle.wait()

	def _start_check(self):
		#Marked as under way at once, not when the task first runs, so that
		#anything waiting for the server to be idle waits for this check
		self._checking = True
		self._idle.clear()
		self._spawn(self._check_until_settled())

	async def _check_until_settled(self):
		try:
			while True:
				self._again = False
				try:
					await self._check()
				except Exception as err:
					self.log("Could not check for edits: {}".format(err))
				if not self._again:
					break
		finally:
			self._checking = False
			self._idle.set()

	def _areas(self, kind):
		return {c.subscriptions[kind] for c in self.connections if c.subscribed(kind)}

	async def _check(self):
		latest = await self.call_source(self.source.latest)
		if latest[0] < self.latest[0]:
			#The map has gone back, as a reset one does: follow it
			self.log("The map's edits go back to {}, from {}".format(latest[0], self.latest[0]))
			self.latest = latest
			return
		if latest[0] == self.latest[0]:
			return
		first, last = self.latest[0] + 1, latest[0]
		#Asked once for each different area subscribed to, including those
		#subscribed while asking: whether the new edits may concern it, and for
		#records subscribers, the records themselves, in each format asked
		#for, encoded once for all
		answers, records = {None: True}, {}
		while True:
			wanted = self._areas("edits") - set(answers)
			wanted_records = self._areas("records") - set(records)
			if not wanted and not wanted_records:
				break
			for area in wanted:
				answers[area] = await self.call_source(self.source.concerns, first, last, area)
			for wanted_format in wanted_records:
				records[wanted_format] = await self.call_source(self._records_message, first, last, *wanted_format, latest)
		self.latest = latest
		edits = protocol.notification("edits", [protocol.edit_ids(latest)])
		told = 0
		for connection in list(self.connections):
			messages = []
			if connection.subscribed("edits") and answers.get(connection.subscriptions["edits"]):
				messages.append(edits)
			if connection.subscribed("records") and records.get(connection.subscriptions["records"]):
				messages.append(records[connection.subscriptions["records"]])
			if messages:
				connection.send(*messages)
				told += 1
		self.log("Edits up to {}: told {} of {} subscribers".format(last, told,
			sum(1 for c in self.connections if c.subscriptions)))

	def _records_message(self, first, last, area, file_format, latest):
		"""The "records" notification for an area, in a format, or None if no new
		record concerns it. Runs in the source's thread."""
		found = self.source.records(first, last, area, file_format)
		if found is None:
			return None
		count, document = found
		details = {"first_id": first, "last_id": last, "records": count}
		if file_format == "msgpack":
			details["editactivities"] = document
		else:
			details["xml_gz"] = gzip.compress(document, mtime=0)
		details.update(protocol.edit_ids(latest))
		return protocol.notification("records", [details])

	async def _heartbeat(self):
		while True:
			await asyncio.sleep(self.limits.heartbeat)
			#Edits announced while the database connection was being remade are
			#found here
			self.edits_changed()
			message = protocol.notification("heartbeat", [protocol.edit_ids(self.latest)])
			for connection in list(self.connections):
				if connection.subscriptions:
					connection.send(message)
