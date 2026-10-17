"""Tests for the edit notification server.

Most run the server, in a thread of its own, against a stand-in for the map's
edits, and talk to it over TCP with notify/client.py. The last few use a
scratch map in the map database, as the stored extract tests do: the trigger
that announces edits, and the whole path from an upload to a client.
"""
import asyncio
import json
import select
import threading
import time
from unittest import mock

import msgpack
import pgmap
import psycopg2
from psycopg2 import sql
from django.test import SimpleTestCase

import querymap.test_dbextract as extract_tests
from notify import protocol
from notify.client import NotifyClient
from notify.server import Connection, EditNotifier, EditSource, Limits, PgmapEditSource

class FakeSource(EditSource):
	"""Edits as a test says they are."""

	def __init__(self, latest=(10, 5)):
		self.ids = latest
		self.areas = set() #Areas the new edits concern
		self.asked = []

	def latest(self):
		return self.ids

	def concerns(self, first_id, last_id, bbox):
		self.asked.append((first_id, last_id, bbox))
		return bbox in self.areas

class ServerThread:
	"""An EditNotifier running in a thread of its own, on a free port."""

	def __init__(self, source, limits=None, connection_string=None, channel=None):
		self.messages = []
		self.loop = asyncio.new_event_loop()
		self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
		self.thread.start()
		self.notifier = EditNotifier(source, limits or Limits(), log=self.messages.append)
		self.run(self.notifier.start("127.0.0.1", 0, connection_string, channel))
		self.port = self.notifier.port()

	def run(self, coroutine):
		return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(10)

	def edits_changed(self):
		self.loop.call_soon_threadsafe(self.notifier.edits_changed)

	def stop(self):
		self.run(self.notifier.stop())
		self.loop.call_soon_threadsafe(self.loop.stop)
		self.thread.join(10)
		self.loop.close()

	def connect(self, hello=True):
		client = NotifyClient("127.0.0.1", self.port, timeout=10)
		if hello:
			client.call("hello", {"protocol": 1, "client": "tests"})
		return client

def closed(client, wait=5):
	"""Whether the server has closed the connection, waiting up to wait seconds."""
	client.socket.settimeout(wait)
	try:
		while True:
			data = client.socket.recv(65536)
			if not data:
				return True
	except (ConnectionError, OSError):
		return True

class ServerTests(SimpleTestCase):

	def start(self, source=None, **limits):
		self.source = source or FakeSource()
		self.server = ServerThread(self.source, Limits(**limits))
		self.addCleanup(self.server.stop)
		return self.server

	def connect(self, hello=True):
		client = self.server.connect(hello)
		self.addCleanup(client.close)
		return client

	def test_hello(self):
		self.start()
		client = self.connect(hello=False)
		# Nothing but hello first
		with self.assertRaisesRegex(RuntimeError, r"Say hello first \(-32000\)"):
			client.call("subscribe")
		with self.assertRaisesRegex(RuntimeError, r"speaks protocol 1 \(-32001\)"):
			client.call("hello", {"protocol": 2})
		self.assertEqual(client.call("hello", {"protocol": 1, "client": "tests"}),
			{"protocol": 1, "server": "pycrocosm editnotifier", "edit_activity_id": 10, "atomic_edit_id": 5})
		self.assertEqual(client.call("ping"), "pong")

	def test_bad_requests(self):
		self.start()
		client = self.connect()
		for method, params, message in (("nothing", [], "No method nothing"),
				("subscribe", [{"bbox": [1, 2, 3]}], "four numbers"),
				("subscribe", [{"bbox": [2, 0, 1, 1]}], "west <= east"),
				("subscribe", [{"bbox": [0, 0, 1, 100]}], "latitude -90 to 90"),
				("subscribe", [{"bbox": [0, 0, float("nan"), 1]}], "finite"),
				("subscribe", [{"area": 1}], "Unknown option: area"),
				("subscribe", [1, 2], "at most one parameter")):
			with self.subTest(method=method, params=params):
				with self.assertRaisesRegex(RuntimeError, message):
					client.call(method, *params)
		# Not a request at all
		client.socket.sendall(msgpack.packb([2, "edits", []]))
		error = next(client._messages())
		self.assertEqual((error[0], error[2]["code"]), (1, protocol.INVALID_REQUEST))
		# The connection is still usable after all of those
		self.assertEqual(client.call("ping"), "pong")

	def test_subscribers_hear_of_new_edits(self):
		server = self.start()
		everything = self.connect()
		self.assertEqual(everything.call("subscribe"), {"edit_activity_id": 10, "atomic_edit_id": 5})
		self.source.ids = (12, 7)
		server.edits_changed()
		self.assertEqual(everything.next_notification(), ("edits", [{"edit_activity_id": 12, "atomic_edit_id": 7}]))
		# Nothing new, nothing said
		server.edits_changed()
		self.assertEqual(everything.call("ping"), "pong")
		self.assertEqual(everything.waiting, [])
		# Unsubscribed, nothing more
		self.assertIs(everything.call("unsubscribe"), True)
		self.source.ids = (13, 8)
		server.edits_changed()
		self.assertEqual(everything.call("ping"), "pong")
		self.assertEqual(everything.waiting, [])

	def test_only_edits_that_may_concern_the_area(self):
		server = self.start()
		near = self.connect()
		near_too = self.connect()
		far = self.connect()
		everything = self.connect()
		near.call("subscribe", {"bbox": [0, 0, 1, 1]})
		near_too.call("subscribe", {"bbox": [0, 0, 1, 1]})
		far.call("subscribe", {"bbox": [10, 10, 11, 11]})
		everything.call("subscribe")
		self.source.areas = {(0.0, 0.0, 1.0, 1.0)}
		self.source.ids = (14, 9)
		server.edits_changed()
		expected = ("edits", [{"edit_activity_id": 14, "atomic_edit_id": 9}])
		for client in (near, near_too, everything):
			self.assertEqual(client.next_notification(), expected)
		# The far subscriber was told nothing: its answer to this comes first
		self.assertEqual(far.call("ping"), "pong")
		self.assertEqual(far.waiting, [])
		# Each area was asked about once, for the edits since the last notice
		self.assertEqual(sorted(self.source.asked), [(11, 14, (0.0, 0.0, 1.0, 1.0)), (11, 14, (10.0, 10.0, 11.0, 11.0))])

	def test_heartbeats(self):
		self.start(heartbeat=0.1)
		client = self.connect()
		client.call("subscribe")
		self.assertEqual(client.next_notification(), ("heartbeat", [{"edit_activity_id": 10, "atomic_edit_id": 5}]))
		# The periodic check also finds edits that no one announced
		self.source.ids = (11, 6)
		while True:
			method, params = client.next_notification()
			if method == "edits":
				break
		self.assertEqual(params, [{"edit_activity_id": 11, "atomic_edit_id": 6}])

	def test_a_map_that_goes_back(self):
		server = self.start()
		client = self.connect()
		client.call("subscribe")
		self.source.ids = (3, 2)
		server.edits_changed()
		# Followed, without a notification; a later edit is announced from there
		self.assertEqual(self.connect().call("hello")["edit_activity_id"], 3)
		self.assertEqual(client.waiting, [])
		self.source.ids = (4, 3)
		server.edits_changed()
		self.assertEqual(client.next_notification(), ("edits", [{"edit_activity_id": 4, "atomic_edit_id": 3}]))

	# Limits

	def test_connections_from_one_address(self):
		self.start(max_per_address=2)
		first, second = self.connect(), self.connect()
		third = NotifyClient("127.0.0.1", self.server.port, timeout=5)
		self.addCleanup(third.close)
		self.assertTrue(closed(third))
		self.assertTrue(any("2 connections from it already" in m for m in self.server.messages))
		# A place comes free when one goes
		first.close()
		deadline = time.time() + 5
		while time.time() < deadline:
			fourth = NotifyClient("127.0.0.1", self.server.port, timeout=5)
			self.addCleanup(fourth.close)
			try:
				self.assertEqual(fourth.call("hello")["protocol"], 1)
				break
			except (ConnectionError, OSError):
				time.sleep(0.05)
		else:
			self.fail("No place came free")
		self.assertEqual(second.call("ping"), "pong")

	def test_connections_in_all(self):
		self.start(max_connections=1)
		self.connect()
		other = NotifyClient("127.0.0.1", self.server.port, timeout=5)
		self.addCleanup(other.close)
		self.assertTrue(closed(other))

	def test_messages_too_large(self):
		self.start(max_message_bytes=64)
		client = self.connect()
		client.socket.sendall(msgpack.packb([0, 1, "ping", ["x" * 100]]))
		self.assertTrue(closed(client))

	def test_too_many_requests(self):
		self.start(request_burst=3, requests_per_second=0.001)
		client = self.connect() #hello is the first
		client.call("ping")
		client.call("ping")
		with self.assertRaisesRegex(RuntimeError, r"More than .* requests a second \(-32002\)"):
			client.call("ping")
		self.assertTrue(closed(client))

	def test_time_to_hello_and_subscribe(self):
		self.start(hello_timeout=0.2, subscribe_timeout=0.3)
		silent = self.connect(hello=False)
		self.assertTrue(closed(silent))
		greeted = self.connect()
		self.assertTrue(closed(greeted))
		subscribed = self.connect()
		subscribed.call("subscribe")
		time.sleep(0.5)
		self.assertEqual(subscribed.call("ping"), "pong")

	def test_clients_that_do_not_read(self):
		# Data piling up for a client means it is dropped, not kept waiting for
		notifier = EditNotifier(FakeSource(), Limits(max_write_buffer=100), log=lambda message: None)
		connection = Connection(notifier)
		transport = mock.Mock()
		transport.is_closing.return_value = False
		transport.get_write_buffer_size.side_effect = [50, 150]
		connection.transport = transport
		connection.send(b"x")
		transport.abort.assert_not_called()
		connection.send(b"x")
		transport.abort.assert_called_once()

class MapTests(SimpleTestCase):
	"""With a scratch map, as the stored extract tests make one."""
	databases = {"default"}
	setUp = extract_tests.DbExtractTestCase.setUp
	drop_tables = extract_tests.DbExtractTestCase.drop_tables
	upload = extract_tests.DbExtractTestCase.upload
	create_node = extract_tests.DbExtractTestCase.create_node
	set_schema = extract_tests.DbExtractTestCase.set_schema

	def latest(self):
		t = self.map.GetTransaction("ACCESS SHARE")
		try:
			ids = t.GetLatestEditIds()
			return {"edit_activity_id": int(ids[0]), "atomic_edit_id": int(ids[1])}
		finally:
			t.Abort()

	def triggers(self):
		with self.db.cursor() as cursor:
			cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = %s",
				[self.prefixes[1] + "edit_activity", self.prefixes[1] + "notify_edits"])
			return cursor.fetchone()[0]

	def test_edits_are_announced(self):
		listener = psycopg2.connect(self.connection_string)
		listener.autocommit = True
		self.addCleanup(listener.close)
		with listener.cursor() as cursor:
			cursor.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.prefixes[1] + "edits")))
		self.create_node(0.5)
		self.assertNotEqual(select.select([listener], [], [], 10), ([], [], []))
		listener.poll()
		payloads = [json.loads(n.payload) for n in listener.notifies]
		# The last tells where the map has got to
		self.assertEqual(payloads[-1], self.latest())

	def test_schema_14_has_no_trigger(self):
		self.assertEqual(self.triggers(), 1)
		self.set_schema(14)
		self.assertEqual(self.triggers(), 0)
		self.set_schema(15)
		self.assertEqual(self.triggers(), 1)

	def test_from_an_upload_to_a_client(self):
		source = PgmapEditSource(lambda: pgmap.PgMap(self.connection_string, self.prefixes[0],
			self.prefixes[1], self.prefixes[1], self.prefixes[2]))
		# A long heartbeat, so only the database's notification can bring news soon
		server = ServerThread(source, Limits(heartbeat=60), self.connection_string, self.prefixes[1] + "edits")
		self.addCleanup(server.stop)
		near, far = server.connect(), server.connect()
		self.addCleanup(near.close)
		self.addCleanup(far.close)
		self.assertEqual(near.call("subscribe", {"bbox": [-1, -1, 1, 1]}), self.latest())
		far.call("subscribe", {"bbox": [10, 10, 11, 11]})
		self.create_node(0.5)
		self.assertEqual(near.next_notification(), ("edits", [self.latest()]))
		self.assertEqual(far.call("ping"), "pong")
		self.assertEqual(far.waiting, [])
		self.assertTrue(any(m.startswith("Listening for edits on") for m in server.messages))
