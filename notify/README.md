Edit notifications
==================

A TCP server that tells subscribed clients when the map has new edits, as they are made. A connection can hold two subscriptions, each at most once: to `edits` notifications, which say how far the map has got, after which a client fetches the edits through the replication API (`replication/edit_activities`); and to the `records` themselves, which the server sends as they are made, as the same gzipped XML the API gives, or as msgpack with their objects o5m encoded. Each subscription can be limited to an area, a bbox or a WKT polygon or multipolygon, to hear only of edits that may concern it. The protocol is [msgpack-RPC](https://github.com/msgpack-rpc/msgpack-rpc/blob/master/spec.md) over a plain, unencrypted TCP connection.

How it works
------------

1. Each time edit activity is recorded, a trigger on the map's `edit_activity` table sends a PostgreSQL notification on the channel `<prefix>edits` (`planet_mod_edits` with the usual prefix), with the highest edit activity ID and atomic edit ID added, as JSON. PostgreSQL delivers it when the edit's transaction commits, and not at all if it is rolled back, so whoever hears it can read the edit straight away. The trigger is part of schema version 15 (see pgmap's README, "Extract boundaries (schema version 15)" and `DbSetEditNotifications`); a map that is already at 15 gains it the next time its schema is set to 15 with the admin tool. The server warns when it starts if the trigger is missing: it then finds edits only at each heartbeat, not as they are made.
2. The server LISTENs on that channel. When a notification comes, it asks the map for its latest edit IDs, and for each different area subscribed to, whether the new edits may concern it. It then sends an `edits` notification to each edits subscriber they concern. For records subscribers, it reads the records concerning each area once for each format asked for, as the API writes them (gzipping XML), and sends them to everyone subscribed to that area in that format. A bbox is tested as `replication/edit_activities?bbox=` tests it: against the boxes recorded with each edit. A WKT area is tested against the shape itself: the box of one of the edit's objects must meet it, not only the rectangle around it.
3. PostgreSQL keeps no notifications for a listener that is not there. So the server also checks for new edits at every heartbeat (30 seconds by default), which finds any that were announced while its database connection was being remade.

An `edits` notification is a hint: "there are edits up to these IDs that may concern you". It carries no edits, several edits may come as one notification, and a client that was not connected misses it. A `records` notification carries the records, and says which range of edit activity IDs it covers. Either way, a client keeps its own record of how far it has got, and after connecting, or whenever in doubt (after being dropped for falling behind, say), catches up from `replication/edit_ids` and `replication/edit_activities`.

Running it
----------

The server is a manage.py command, so it uses the same settings and map database as the web server:

	docker compose --profile notify up -d notifier

The `notifier` service in `docker-compose.yml` runs `python3 manage.py editnotifier --port 7500` from the same image as the web server, and publishes port 7500. It is in the `notify` profile, so a plain `docker compose up` does not start it. Only one runs at a time: a second exits at once, saying so. It stops cleanly on SIGTERM (`docker compose stop notifier`). Without Docker, run the command under systemd or similar, restarting it if it stops.

It needs a direct connection to PostgreSQL: a connection pooler such as PgBouncer in transaction mode does not pass on notifications.

The options limit what clients send, and how many connect. What the server sends is not limited, in size or how often, but a client must keep up with it. One that leaves more than `--write-high-water` bytes unread counts as behind; the server goes on sending, so a client that catches up within `--behind-timeout` seconds misses nothing, and one that does not is dropped, what was waiting for it thrown away. It then catches up through the replication API when it connects again. A single large message counts like any other, so a client reading one only has to keep reading. A client that has gone without a word, its machine switched off, say, is noticed by TCP keepalive: after `--keepalive` seconds of quiet, then a probe every 10 seconds, giving up after 6. All have defaults that suit most servers:

| Option | Default | |
|---|---|---|
| `--host`, `--port` | `0.0.0.0`, `7500` | Where to listen |
| `--max-connections` | 64 | Connections at once, in all |
| `--max-per-address` | 10 | Connections at once from one address |
| `--max-message-bytes` | 65536 | The largest request a client may send, WKT and all; a larger one closes the connection |
| `--requests-per-second`, `--request-burst` | 5, 20 | Requests each connection may make, on average and at once; more are refused and the connection closed |
| `--hello-timeout` | 10 | Seconds a new connection has to say hello |
| `--subscribe-timeout` | 60 | Seconds it then has to subscribe to something (and after giving up every subscription, to subscribe again) |
| `--write-high-water` | 1048576 | Bytes unread by a client before it counts as behind |
| `--behind-timeout` | 60 | Seconds a client may stay behind before it is dropped |
| `--keepalive` | 60 | Seconds a connection may be quiet before TCP checks the client is still there; one that has gone is noticed about a minute later |
| `--heartbeat` | 30 | Seconds between heartbeats, and between checks for missed edits |
| `--max-wkt-points` | 1000 | Points a WKT area may have; a more detailed shape is refused before the map is asked about it |
| `--max-areas` | 32 | Different areas followed at once, by all connections together (0 for no limit). The map is asked about each at every edit. An area already followed, and everywhere, can always be subscribed to |
| `--bbox-grid` | 0.01 | Degrees each subscription's bbox is widened out to a multiple of, so subscribers to nearby areas share one (0 leaves bboxes as given) |
| `--query-timeout` | 10 | Seconds each query of the map may take (0 for no limit) |
| `--records-per-message` | 100 | Records in one `records` notification, at most; more are sent as several |

What clients subscribe to decides what the server asks the map, so that is limited too: how detailed a WKT area may be, how many different areas are followed, and how long each query may take. A query that fails, by timing out say, holds up only its own area. Its `edits` subscribers are told of the edits anyway, since a notification only says they may concern them; its `records` subscribers are dropped, since they would otherwise miss records, and catch up through the API when they connect again.

The connection is not encrypted, and the server does not ask who a client is: anyone who can reach the port can subscribe, and learns no more than the public replication API tells. Open the port in a firewall only to the clients that need it.

Protocol
--------

Messages are msgpack arrays, one after another on the connection:

	request       [0, msgid, method, params]    client to server
	response      [1, msgid, error, result]     server to client
	notification  [2, method, params]           server to client

`params` is an array. `error` is nil on success, or a map with an integer `code` and a `message`.

Requests, of which `hello` must come first:

| Request | Result |
|---|---|
| `hello([{"protocol": 2, "client": "name"}])` | `{"protocol": 2, "server": "pycrocosm editnotifier", "edit_activity_id": N, "atomic_edit_id": M}` |
| `subscribe_edits([])`, `subscribe_edits([{"bbox": [west, south, east, north]}])` or `subscribe_edits([{"wkt": "POLYGON((...))"}])` | `{"edit_activity_id": N, "atomic_edit_id": M}`: the map's latest edit IDs, read from the map when subscribing. Edits after these are announced, with `edits` notifications; for those up to them, use the replication API. |
| `subscribe_records(...)`, with the same parameters, and optionally `"format": "xml"` (the default) or `"msgpack"` | The same; the records of edits after these are sent, with `records` notifications. Nothing up to these IDs is sent, and nothing after them is missed. |
| `unsubscribe_edits([])`, `unsubscribe_records([])` | `true` |
| `ping([])` | `"pong"` |

A connection holds at most one subscription of each kind: subscribing to a kind again needs unsubscribing from it first. The two kinds are separate and may have different areas. A WKT area must be a valid polygon or multipolygon no wider than 180 degrees of longitude, as an extract's boundary must, with no more than `--max-wkt-points` points, and is checked by PostGIS when subscribing. A bbox is widened out to the `--bbox-grid`, 0.01 degrees unless the server says otherwise, so a subscriber may hear of edits a little outside it. Left out, the area is everywhere.

Notifications:

| Notification | |
|---|---|
| `edits([{"edit_activity_id": N, "atomic_edit_id": M}])` | New edits up to these IDs may concern the subscription |
| `records([{"first_id": A, "last_id": B, "records": n, "edit_activity_id": N, "atomic_edit_id": M, "xml_gz": <bin>}])` | The `n` records with IDs from A to B that may concern the subscription, as a gzipped XML document in the form `replication/edit_activities` gives (`xml_gz` is msgpack binary). More than `--records-per-message` are split over several notifications. The ranges of one notification and the next follow on from each other, except where a range held nothing concerning the area, which is not sent. |
| `records([{"first_id": A, "last_id": B, "records": n, "edit_activity_id": N, "atomic_edit_id": M, "editactivities": [...]}])` | The same, for a subscription in the `msgpack` format: the records as maps, as `replication/edit_activities?format=msgpack.gz` gives them once ungzipped, their objects o5m encoded. The notification itself is not gzipped. |
| `heartbeat([{"edit_activity_id": N, "atomic_edit_id": M}])` | Sent to subscribers at every heartbeat, with where the map has got to |

Error codes, after JSON-RPC's: -32700 a message that cannot be read (the connection is then closed), -32600 not a request, -32601 no such method, -32602 bad parameters (including a WKT area PostGIS refuses, with the reason), -32000 hello has not been sent, -32001 an unsupported protocol version, -32002 too many requests (the connection is then closed), -32003 already subscribed to that kind, -32004 not subscribed to that kind, -32005 the server is following as many different areas as it will (subscribe to a larger area, or everywhere).

A client
--------

`client.py` is a small client for trying the server out; it subscribes and prints what it is sent:

	python3 notify/client.py localhost 7500 --bbox -1.11,50.77,-1.03,50.86
	python3 notify/client.py localhost 7500 --records --wkt-file town.wkt --xml
	python3 notify/client.py localhost 7500 --records --msgpack --bbox -1.11,50.77,-1.03,50.86
	python3 notify/client.py localhost 7500 --edits --records --bbox -1.11,50.77,-1.03,50.86

Its `NotifyClient` class is a starting point for a client of your own. It needs the `msgpack` package.

Tests
-----

	docker compose run --rm web python3 manage.py test notify

Most run the server against a stand-in for the map; the rest use a scratch map, to check that an upload's notification reaches a subscriber, that WKT areas are checked and followed, and that the records sent are those the API gives.
