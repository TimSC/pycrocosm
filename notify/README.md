Edit notifications
==================

A TCP server that tells subscribed clients when the map has new edits, as they are made. Clients say which area they care about, and hear only of edits that may concern it; they then fetch the edits themselves through the replication API (`replication/edit_activities`). The protocol is [msgpack-RPC](https://github.com/msgpack-rpc/msgpack-rpc/blob/master/spec.md) over a plain, unencrypted TCP connection.

How it works
------------

1. Each time edit activity is recorded, a trigger on the map's `edit_activity` table sends a PostgreSQL notification on the channel `<prefix>edits` (`planet_mod_edits` with the usual prefix), with the highest edit activity ID and atomic edit ID added, as JSON. PostgreSQL delivers it when the edit's transaction commits, and not at all if it is rolled back, so whoever hears it can read the edit straight away. The trigger is part of schema version 15 (see pgmap's README, "Extract boundaries (schema version 15)" and `DbSetEditNotifications`); a map that is already at 15 gains it the next time its schema is set to 15 with the admin tool.
2. The server LISTENs on that channel. When a notification comes, it asks the map for its latest edit IDs, and for each different area subscribed to, whether the new edits may concern it, using the same test as `replication/edit_activities?bbox=`. It then sends an `edits` notification to each subscriber they concern.
3. PostgreSQL keeps no notifications for a listener that is not there. So the server also checks for new edits at every heartbeat (30 seconds by default), which finds any that were announced while its database connection was being remade.

A notification is a hint: "there are edits up to these IDs that may concern you". It carries no edits, several edits may come as one notification, and a client that was not connected misses it. A client keeps its own record of how far it has got, and after connecting, or whenever in doubt, catches up from `replication/edit_ids` and `replication/edit_activities`.

Running it
----------

The server is a manage.py command, so it uses the same settings and map database as the web server:

	docker compose --profile notify up -d notifier

The `notifier` service in `docker-compose.yml` runs `python3 manage.py editnotifier --port 7500` from the same image as the web server, and publishes port 7500. It is in the `notify` profile, so a plain `docker compose up` does not start it. Only one runs at a time: a second exits at once, saying so. It stops cleanly on SIGTERM (`docker compose stop notifier`). Without Docker, run the command under systemd or similar, restarting it if it stops.

It needs a direct connection to PostgreSQL: a connection pooler such as PgBouncer in transaction mode does not pass on notifications.

Options, all with defaults that suit most servers:

| Option | Default | |
|---|---|---|
| `--host`, `--port` | `0.0.0.0`, `7500` | Where to listen |
| `--max-connections` | 1000 | Connections at once, in all |
| `--max-per-address` | 10 | Connections at once from one address |
| `--max-message-bytes` | 4096 | The largest request a client may send; a larger one closes the connection |
| `--requests-per-second`, `--request-burst` | 5, 20 | Requests each connection may make, on average and at once; more are refused and the connection closed |
| `--hello-timeout` | 10 | Seconds a new connection has to say hello |
| `--subscribe-timeout` | 60 | Seconds it then has to subscribe (and after unsubscribing, to subscribe again) |
| `--max-write-buffer` | 262144 | Bytes waiting for a client before it is dropped as not reading what it is sent |
| `--heartbeat` | 30 | Seconds between heartbeats, and between checks for missed edits |

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
| `hello([{"protocol": 1, "client": "name"}])` | `{"protocol": 1, "server": "pycrocosm editnotifier", "edit_activity_id": N, "atomic_edit_id": M}` |
| `subscribe([])` or `subscribe([{"bbox": [west, south, east, north]}])` | `{"edit_activity_id": N, "atomic_edit_id": M}`, where the map has got to: edits after these are announced. Replaces any earlier subscription. |
| `unsubscribe([])` | `true` |
| `ping([])` | `"pong"` |

Notifications:

| Notification | |
|---|---|
| `edits([{"edit_activity_id": N, "atomic_edit_id": M}])` | New edits up to these IDs may concern the subscription |
| `heartbeat([{"edit_activity_id": N, "atomic_edit_id": M}])` | Sent to subscribers at every heartbeat, with where the map has got to |

Error codes, after JSON-RPC's: -32700 a message that cannot be read (the connection is then closed), -32600 not a request, -32601 no such method, -32602 bad parameters, -32000 hello has not been sent, -32001 an unsupported protocol version, -32002 too many requests (the connection is then closed).

A client
--------

`client.py` is a small client for trying the server out; it subscribes and prints what it is sent:

	python3 notify/client.py localhost 7500 --bbox -1.11,50.77,-1.03,50.86

Its `NotifyClient` class is a starting point for a client of your own. It needs the `msgpack` package.

Tests
-----

	docker compose run --rm web python3 manage.py test notify

Most run the server against a stand-in for the map; the rest use a scratch map, to check that an upload's notification reaches a subscriber.
