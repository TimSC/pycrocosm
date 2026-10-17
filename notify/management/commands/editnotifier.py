import asyncio
import signal

import psycopg2

from django.conf import settings
from django.core.management.base import BaseCommand

from notify.server import EditNotifier, Limits, PgmapEditSource
from pycrocosm import mapdb
from pycrocosm.singlerun import one_at_a_time

class Command(BaseCommand):
	help = ("Run the edit notification server: a TCP server that tells subscribed clients when the map has "
		"new edits, or sends them the edit activity records, speaking msgpack-RPC. Runs until stopped. "
		"The options limit what clients send, and how far behind with what they are sent they may fall; "
		"what the server sends is not limited. See notify/README.md.")

	def add_arguments(self, parser):
		defaults = Limits()
		parser.add_argument("--host", default="0.0.0.0", help="The address to listen on (default all)")
		parser.add_argument("--port", type=int, default=7500, help="The port to listen on (default 7500)")
		parser.add_argument("--max-connections", type=int, default=defaults.max_connections,
			help="Connections allowed at once (default {})".format(defaults.max_connections))
		parser.add_argument("--max-per-address", type=int, default=defaults.max_per_address,
			help="Connections allowed at once from one address (default {})".format(defaults.max_per_address))
		parser.add_argument("--max-message-bytes", type=int, default=defaults.max_message_bytes,
			help="The largest request a client may send, WKT and all (default {})".format(defaults.max_message_bytes))
		parser.add_argument("--requests-per-second", type=float, default=defaults.requests_per_second,
			help="Requests each connection may make a second, on average (default {})".format(defaults.requests_per_second))
		parser.add_argument("--request-burst", type=int, default=defaults.request_burst,
			help="Requests each connection may make at once (default {})".format(defaults.request_burst))
		parser.add_argument("--hello-timeout", type=float, default=defaults.hello_timeout,
			help="Seconds a new connection has to say hello (default {})".format(defaults.hello_timeout))
		parser.add_argument("--subscribe-timeout", type=float, default=defaults.subscribe_timeout,
			help="Seconds a connection then has to subscribe to something (default {})".format(defaults.subscribe_timeout))
		parser.add_argument("--write-high-water", type=int, default=defaults.write_high_water,
			help="Bytes unread by a client before it counts as behind (default {})".format(defaults.write_high_water))
		parser.add_argument("--behind-timeout", type=float, default=defaults.behind_timeout,
			help="Seconds a client may stay behind before it is dropped (default {})".format(defaults.behind_timeout))
		parser.add_argument("--keepalive", type=int, default=defaults.keepalive,
			help="Seconds a connection may be idle before TCP checks the client is still there; a client that "
				"has gone is noticed about a minute after that (default {})".format(defaults.keepalive))
		parser.add_argument("--heartbeat", type=float, default=defaults.heartbeat,
			help="Seconds between heartbeats and checks for missed edits (default {})".format(defaults.heartbeat))
		parser.add_argument("--max-wkt-points", type=int, default=defaults.max_wkt_points,
			help="Points a WKT area may have (default {})".format(defaults.max_wkt_points))
		parser.add_argument("--max-areas", type=int, default=defaults.max_areas,
			help="Different areas followed at once, by all connections together; 0 for no limit (default {})".format(
				defaults.max_areas))
		parser.add_argument("--bbox-grid", type=float, default=defaults.bbox_grid,
			help="Degrees a subscription's bbox is widened out to a multiple of, so nearby ones share an area; "
				"0 to leave them as given (default {})".format(defaults.bbox_grid))
		parser.add_argument("--query-timeout", type=float, default=defaults.query_timeout,
			help="Seconds each query of the map may take; 0 for no limit (default {})".format(defaults.query_timeout))
		parser.add_argument("--records-per-message", type=int, default=defaults.records_per_message,
			help="Records in one records notification, at most (default {})".format(defaults.records_per_message))

	@one_at_a_time
	def handle(self, *args, **options):
		limits = Limits(**{field: options[field] for field in Limits.__dataclass_fields__})
		prefix = settings.MAP_DATABASE[mapdb.ACTIVE_DB]
		# The channel pgmap's trigger announces this map's edits on
		channel = prefix + "edits"
		self.check_trigger(prefix, limits)
		asyncio.run(self.serve(options["host"], options["port"], limits, channel))

	def check_trigger(self, prefix, limits):
		"""Warns if the map does not announce its edits, when they are found
		only at each heartbeat, rather than as they are made."""
		with psycopg2.connect(mapdb.make_connection_string()) as connection, connection.cursor() as cursor:
			cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass(%s) AND tgname = %s AND tgenabled <> 'D'",
				[prefix + "edit_activity", prefix + "notify_edits"])
			found = cursor.fetchone()[0] > 0
		connection.close()
		if not found:
			self.log("Warning: {}edit_activity has no {}notify_edits trigger, so edits will be found only at each "
				"heartbeat (every {}s), not as they are made. Set the map's schema to 15 with pgmap's admin tool "
				"(option g) to add it.".format(prefix, prefix, limits.heartbeat))

	async def serve(self, host, port, limits, channel):
		notifier = EditNotifier(PgmapEditSource(mapdb.make_pgmap, limits.query_timeout), limits, log=self.log)
		await notifier.start(host, port, mapdb.make_connection_string(), channel)
		stopping = asyncio.Event()
		loop = asyncio.get_running_loop()
		for signum in (signal.SIGINT, signal.SIGTERM):
			loop.add_signal_handler(signum, stopping.set)
		await stopping.wait()
		self.log("Stopping")
		await notifier.stop()

	def log(self, message):
		self.stdout.write(message)
		self.stdout.flush()
