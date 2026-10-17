import asyncio
import signal

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

	@one_at_a_time
	def handle(self, *args, **options):
		limits = Limits(**{field: options[field] for field in Limits.__dataclass_fields__})
		# The channel pgmap's trigger announces this map's edits on
		channel = settings.MAP_DATABASE[mapdb.ACTIVE_DB] + "edits"
		asyncio.run(self.serve(options["host"], options["port"], limits, channel))

	async def serve(self, host, port, limits, channel):
		notifier = EditNotifier(PgmapEditSource(mapdb.make_pgmap), limits, log=self.log)
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
