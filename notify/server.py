"""A TCP server that tells subscribed clients when the map has new edits.

PostgreSQL announces each edit with NOTIFY (see DbSetEditNotifications in
pgmap's dbadmin.cpp). The server LISTENs for that, works out which subscribers
the new edits may concern, and sends each of them an "edits" notification
(see protocol.py). It also asks the map for its latest edit IDs now and then,
so nothing is missed while its database connection is being remade.

Everything runs in one asyncio loop. Calls into the map database, which block,
run one at a time in a thread of their own.
"""

import asyncio
import concurrent.futures
import dataclasses
import socket
import time

import msgpack

from . import protocol
from .protocol import ProtocolError

@dataclasses.dataclass
class Limits:
	"""What the server allows, in all and on each connection."""
	max_connections: int = 1000
	max_per_address: int = 10 #Connections from one address at a time
	max_message_bytes: int = 4096 #The largest request a client may send
	requests_per_second: float = 5 #Requests each connection may make, on average...
	request_burst: int = 20 #...and at once
	hello_timeout: float = 10 #Seconds a new connection has to say hello
	subscribe_timeout: float = 60 #Seconds it then has to subscribe
	max_write_buffer: int = 256 * 1024 #Bytes waiting to be sent before a client is too slow
	heartbeat: float = 30 #Seconds between heartbeats, and checks for missed edits

class EditSource:
	"""Where the server learns about edits. Its methods block; the server calls
	them in a thread of their own."""

	def latest(self):
		"""The latest (edit activity ID, atomic edit ID)."""
		raise NotImplementedError

	def concerns(self, first_id, last_id, bbox):
		"""Whether any edit activity record with an ID in the range may concern
		the area (west, south, east, north)."""
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

	def concerns(self, first_id, last_id, bbox):
		import pgmap
		t = self._transaction()
		try:
			results = pgmap.vectorsharedptreditactivity()
			error = pgmap.PgMapError()
			#The same filter as replication/edit_activities?bbox=; one row is enough
			t.QueryEditActivityByIds(first_id, last_id, 0, results, error, 1, pgmap.vectord(list(bbox)), False)
			if error.errStr:
				raise RuntimeError(error.errStr)
			return results.size() > 0
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

class Connection(asyncio.Protocol):
	"""One client."""

	def __init__(self, notifier):
		self.notifier = notifier
		self.limits = notifier.limits
		self.transport = None
		self.address = None
		self.greeted = False
		self.subscribed = False
		self.bbox = None
		self.unpacker = protocol.unpacker(self.limits.max_message_bytes)
		self.tokens = float(self.limits.request_burst)
		self.refilled = time.monotonic()
		self.deadline = None

	# The connection

	def connection_made(self, transport):
		self.transport = transport
		peer = transport.get_extra_info("peername")
		self.address = peer[0] if peer else "?"
		if not self.notifier.admit(self):
			transport.abort()
			return
		sock = transport.get_extra_info("socket")
		if sock is not None:
			#The system notices a peer that has gone without a word
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
		self.deadline = asyncio.get_running_loop().call_later(self.limits.hello_timeout, self._expire, "say hello")

	def connection_lost(self, exc):
		if self.deadline is not None:
			self.deadline.cancel()
		self.notifier.release(self)

	def close(self, reason=None):
		if reason:
			self.notifier.log("Closing {}: {}".format(self.address, reason))
		if self.transport is not None and not self.transport.is_closing():
			self.transport.close()

	def _expire(self, what):
		self.close("did not {} in time".format(what))

	def send(self, data):
		if self.transport is None or self.transport.is_closing():
			return
		self.transport.write(data)
		#A client that does not read what it is sent is not kept waiting for
		if self.transport.get_write_buffer_size() > self.limits.max_write_buffer:
			self.notifier.log("Closing {}: not reading what it is sent".format(self.address))
			self.transport.abort()

	def notify(self, method, params):
		self.send(protocol.notification(method, params))

	# Requests

	def data_received(self, data):
		try:
			self.unpacker.feed(data)
		except msgpack.BufferFull:
			self.close("sent a message larger than {} bytes".format(self.limits.max_message_bytes))
			return
		while True:
			try:
				message = next(self.unpacker)
			except StopIteration:
				return
			except (msgpack.UnpackException, ValueError) as err:
				self.send(protocol.response(None, error=ProtocolError(protocol.PARSE_ERROR, "Cannot read: {}".format(err)).as_error()))
				self.close("sent what cannot be read")
				return
			if not self._allowed():
				self.send(protocol.response(self._msgid(message), error=ProtocolError(protocol.TOO_MANY_REQUESTS,
					"More than {} requests a second".format(self.limits.requests_per_second)).as_error()))
				self.close("made too many requests")
				return
			self._handle(message)
			if self.transport.is_closing():
				return

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

	def _handle(self, message):
		msgid = None
		try:
			msgid, method, params = protocol.parse_request(message)
			handler = getattr(self, "_request_" + method, None)
			if handler is None:
				raise ProtocolError(protocol.METHOD_NOT_FOUND, "No method {}".format(method))
			if method != "hello" and not self.greeted:
				raise ProtocolError(protocol.NOT_READY, "Say hello first")
			result = handler(params)
		except ProtocolError as err:
			if msgid is None:
				msgid = self._msgid(message)
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
			self.deadline.cancel()
			self.deadline = asyncio.get_running_loop().call_later(self.limits.subscribe_timeout, self._expire, "subscribe")
		result = {"protocol": protocol.PROTOCOL_VERSION, "server": "pycrocosm editnotifier"}
		result.update(protocol.edit_ids(self.notifier.latest))
		return result

	def _request_subscribe(self, params):
		given = protocol.options(params, ("bbox",))
		self.bbox = protocol.parse_bbox(given["bbox"]) if given.get("bbox") is not None else None
		self.subscribed = True
		if self.deadline is not None:
			self.deadline.cancel()
			self.deadline = None
		return protocol.edit_ids(self.notifier.latest)

	def _request_unsubscribe(self, params):
		protocol.options(params, ())
		self.subscribed = False
		self.bbox = None
		#An idle connection is not kept for ever
		if self.deadline is None:
			self.deadline = asyncio.get_running_loop().call_later(self.limits.subscribe_timeout, self._expire, "subscribe")
		return True

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
		self._tasks = set()

	async def _call(self, method, *args):
		return await asyncio.get_running_loop().run_in_executor(self._executor, method, *args)

	async def start(self, host, port, connection_string=None, channel=None):
		"""Starts serving. With a connection string and channel, LISTENs for edits
		there; otherwise only the periodic check notices them."""
		self.latest = await self._call(self.source.latest)
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
		self._spawn(self._check_until_settled())

	async def _check_until_settled(self):
		self._checking = True
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

	async def _check(self):
		latest = await self._call(self.source.latest)
		if latest[0] < self.latest[0]:
			#The map has gone back, as a reset one does: follow it
			self.log("The map's edits go back to {}, from {}".format(latest[0], self.latest[0]))
			self.latest = latest
			return
		if latest[0] == self.latest[0]:
			return
		first, last = self.latest[0] + 1, latest[0]
		#Whether the new edits concern each area subscribed to, asked once for
		#each different area, including those subscribed while asking
		answers = {}
		while True:
			wanted = {c.bbox for c in self.connections if c.subscribed and c.bbox is not None} - set(answers)
			if not wanted:
				break
			for bbox in wanted:
				answers[bbox] = await self._call(self.source.concerns, first, last, bbox)
		self.latest = latest
		message = protocol.notification("edits", [protocol.edit_ids(latest)])
		told = 0
		for connection in list(self.connections):
			if connection.subscribed and (connection.bbox is None or answers.get(connection.bbox)):
				connection.send(message)
				told += 1
		self.log("Edits up to {}: told {} of {} subscribers".format(last, told,
			sum(1 for c in self.connections if c.subscribed)))

	async def _heartbeat(self):
		while True:
			await asyncio.sleep(self.limits.heartbeat)
			#Edits announced while the database connection was being remade are
			#found here
			self.edits_changed()
			message = protocol.notification("heartbeat", [protocol.edit_ids(self.latest)])
			for connection in list(self.connections):
				if connection.subscribed:
					connection.send(message)
