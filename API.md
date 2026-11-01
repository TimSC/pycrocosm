Non-standard API endpoints
==========================

pycrocosm implements the [OpenStreetMap API v0.6](https://wiki.openstreetmap.org/wiki/API_v0.6). This document covers what it offers beyond that API, across all its Django apps, and the tile server pycromap beside it. An endpoint counts as standard if [openstreetmap-website's routes](https://github.com/openstreetmap/openstreetmap-website/blob/master/config/routes.rb) have it.

These endpoints are specific to pycrocosm, so a client written for openstreetmap.org won't find them there, and they may change as the server does.

| Endpoint | What it gives | Django app |
|---|---|---|
| [`GET /api/0.6/map`](#map-at-a-point-in-time-and-in-other-formats) with `format`, `atomic_edit_id` or `timestamp` | The map in binary formats, or as it was at a past edit or time | `querymap` |
| [`GET /api/0.6/{node,way,relation}/{id}/bbox`](#get-api06nodewayrelationidbbox) | An object's bounding box | `elements` |
| [`GET /replication/edit_ids`](#get-replicationedit_ids) | The latest edit IDs | `replicate` |
| [`GET /replication/changed`](#get-replicationchanged) | Whether an area has changed since an edit | `replicate` |
| [`GET /replication/edit_activities`](#get-replicationedit_activities) | Edit records, by ID, time or area | `replicate` |
| [`GET /replication/edit_activity/{id}`](#get-replicationedit_activityid) | One edit record | `replicate` |
| [`GET /replication/diff`](#get-replicationdiff) | A diff of any time range, up to a day | `replicate` |
| [`GET /replication/now`](#get-replicationnow) | The server's time | `replicate` |
| [`GET /replication/extract/...`](#get-replicationextract) | Download a stored extract | `replicate` |
| [`GET /replication/extracts`, `/replication/planet`](#web-pages) | Lists of extracts and planet dumps (HTML) | `replicate` |
| [`GET /extra/most_active`](#get-extramost_active) | The most active users | `extra` |
| [`GET`, `POST /api/interpreter`](#get-post-apiinterpreter) | Overpass QL queries | `overpass` |
| [`GET /overpass/xapi/...`, `/overpass/xapi_meta`](#get-overpassxapiquery-get-overpassxapi_metaquery) | XAPI queries | `overpass` |
| [`GET /.well-known/oauth-authorization-server`](#get-well-knownoauth-authorization-server) | OAuth2 discovery | `oauth2` |
| [`/api/authorize`, `/api/token`](#apiauthorize-apitoken) | OAuth2, next to the API | `oauth2` |
| [TCP port 7500](#edit-notifications) | Notifications of new edits | `notify` |
| [`GET /tiles/{z}/{x}/{y}.pbf`](#get-tileszxypbf) and others | Vector tiles and the map's configuration | pycromap's `tiles` |

Conventions
-----------

* **Login:** none of these endpoints needs one, unless its section says so.
* **Times** are ISO 8601, such as `2026-01-31T12:00:00Z`, and are taken as UTC unless they give an offset. `extra/most_active` differs: see its section.
* **Edit IDs:** an *edit activity record* describes one change to the map, and has its own ID. An *atomic edit* is one transaction, such as one upload, and may hold several records. `edit_activity_id` and `atomic_edit_id` always mean these two.
* **Errors** are `400` with a short message, unless a section says otherwise.
* **Bounding boxes** are ordered differently depending on the endpoint:

| Where | Order |
|---|---|
| `/api/0.6/map`, `replication/*`, XAPI's `[bbox=...]`, Overpass's `bbox=` parameter | west, south, east, north |
| Inside an Overpass QL query | south, west, north, east |

Map at a point in time, and in other formats
--------------------------------------------

`GET /api/0.6/map` takes the standard `bbox`, and three arguments of its own. The limit on the area (`AREA_MAXIMUM`, 0.25 square degrees by default) applies as usual.

| Argument | Meaning |
|---|---|
| `format` | One of `osm`, `osm.gz`, `json`, `json.gz`, `o5m`, `o5m.gz` or `pbf`. Without it, the answer is XML, or JSON if the path ends in `.json` or the `Accept` header asks for it. All but `osm` and `json` come as a file to save (`map.<format>`). |
| `atomic_edit_id` | The map as it was just after this atomic edit, with each object at its version then. It takes longer the more edits have been made since. |
| `timestamp` | The map as it was at this time: after the latest atomic edit recorded at or before it. A time before the first recorded edit gets `400`. |

`atomic_edit_id` and `timestamp` can't be combined. Both reach back only as far as the edit activity records do, and both work with any `format`.

	curl -o map.pbf "http://localhost:8000/api/0.6/map?bbox=-1.09,50.79,-1.06,50.81&format=pbf"
	curl "http://localhost:8000/api/0.6/map?bbox=-1.09,50.79,-1.06,50.81&timestamp=2026-01-31T12:00:00Z"

Ways and relations are found through their nodes in these historical queries. On a map that uses stored boxes (`useBboxInQuery`), the answer for the latest edit can therefore differ a little from the plain map call.

Objects
-------

### `GET /api/0.6/{node,way,relation}/{id}/bbox`

An object's stored bounding box. The answer is always XML, whatever format is asked for.

	<bbox version="0.6" generator="..."><way id="123" minlon="-1.1" minlat="50.7" maxlon="-1.0" maxlat="50.8"/></bbox>

The coordinates are left out if no box is known for the object. An object that doesn't exist gets `404`.

Following edits
---------------

These are how another server, or anything else, keeps up with the map. Their records are what an extract following another server is updated from ([README](README.md#extracts-that-follow-another-server)).

### `GET /replication/edit_ids`

Where the map has got to.

	<?xml version='1.0' encoding='UTF-8'?>
	<editids edit_activity_id="1042" atomic_edit_id="387"/>

### `GET /replication/changed`

Whether an area has been edited since an atomic edit, for something like a map renderer that needs to know whether to draw an area again.

| Argument | Meaning |
|---|---|
| `bbox` | Required: west, south, east, north |
| `since` | Required: an atomic edit ID, or `0` for any edit since the map was imported |

The answer also gives the map's latest IDs, read at the same moment. Keep `atomic_edit_id` to use as the next `since`.

	<?xml version='1.0' encoding='UTF-8'?>
	<changed bbox="-1.1,50.7,-1.0,50.8" since="380" changed="true" edit_activity_id="1042" atomic_edit_id="387"/>

### `GET /replication/edit_activities`

Edit activity records, in ID order.

| Argument | Meaning |
|---|---|
| `id` | One record |
| `first_id`, `last_id` | A range of record IDs, either end optional. Can't be combined with `id`. |
| `atomic_edit_id` | The records of one atomic edit |
| `since`, `until` | A range of times. Can't be combined with the ID arguments. |
| `bbox` | Keep only records that may concern this area: west, south, east, north. Records that don't say where their objects are stay in. |
| `limit` | How many records to send, from 1 to `EDIT_ACTIVITY_MAXIMUM_RECORDS` (100 by default) |
| `format` | `xml` (the default) or `msgpack.gz` |

Which records you get depends on whether you ask for a range:

* **Without a range of IDs or times**, you get the latest records: 20 of them unless `limit` says otherwise (`EDIT_ACTIVITY_DEFAULT_RECORDS`).
* **With a range**, you get records from its start, up to `limit`, or else the maximum. If there are more, the root element has `truncated="true"` and a `next_id`. Continue with `first_id` set to `next_id`, whichever way you chose the records at first.

To follow the whole map, read [`edit_ids`](#get-replicationedit_ids), then ask for each range in turn:

	curl "http://localhost:8000/replication/edit_activities?first_id=1000&last_id=1042"

#### XML

Each `editactivity` holds the objects involved, as OSM XML:

	<editactivities truncated="true" next_id="1021">
	  <editactivity id="1020" changeset="55" timestamp="2026-01-31T12:00:00Z" action="modify" atomic_edit_id="380" block_index="0">
	    <existing>...</existing>
	    <existing_bbox>...</existing_bbox>
	    <updated>...</updated>
	    <updated_bbox>...</updated_bbox>
	    <affectedparents>...</affectedparents>
	    <affectedparents_before_bbox>...</affectedparents_before_bbox>
	    <affectedparents_after_bbox>...</affectedparents_after_bbox>
	    <related>...</related>
	    <relatedparents>...</relatedparents>
	  </editactivity>
	</editactivities>

| Attribute | Meaning |
|---|---|
| `id` | The record's ID |
| `action` | The action of the osmChange block it came from |
| `changeset`, `timestamp` | Left out if the record has none |
| `atomic_edit_id`, `block_index` | The atomic edit, and the position within it. Left out if the record has none. |

| Section | Holds |
|---|---|
| `existing` | The objects as they were before the edit |
| `updated` | The objects as the edit left them |
| `affectedparents` | The ways and relations affected by the change. A parent keeps its version, so its boxes are the only record of how its extent changed. |
| `related` | Objects around those edited |
| `relatedparents` | The relations holding the related nodes. `<relatedparents null="true"/>` means it wasn't recorded, which isn't the same as there being none. |

A box section has one entry for each object of the list it goes with, in order:

	<updated_bbox>
	  <bbox type="node" id="1" minlon="-1.1" minlat="50.7" maxlon="-1.1" maxlat="50.7"/>
	  <bbox type="way" id="2" null="true"/>
	</updated_bbox>

A node's entry is its position. An entry with `null="true"` is for a deleted object. If the whole section has `null="true"`, the boxes weren't recorded.

#### msgpack

`format=msgpack.gz` gives a gzipped msgpack map, with the same content in a form that's quicker to read:

* `editactivities` is the list of records, plus `truncated` and `next_id` if there are more.
* Each record has the attributes above, with `timestamp` in seconds since 1970, UTC.
* Each object list is an o5m document.
* Each box section is a list of `[type, id, min lon, min lat, max lon, max lat]` entries (`[type, id]` for a deleted object), or nil if it wasn't recorded.

### `GET /replication/edit_activity/{id}`

One record, in the same document form as `edit_activities`. It takes `format`. A record that doesn't exist gets `404`.

### Edit notifications

The `notify` app has no URLs: it's a separate TCP server, on port 7500 by default, speaking msgpack-RPC rather than HTTP. It tells subscribers about new edits as they're made, for the whole map or for an area, so that they needn't poll `edit_ids`. See [notify/README.md](notify/README.md) for how to run it, and [notify/protocol.py](notify/protocol.py) for the protocol.

Diffs and time
--------------

### `GET /replication/diff`

An osmChange diff of any time range, up to `REPLICATE_DIFF_MAXIMUM_SECONDS` (a day by default).

| Argument | Meaning |
|---|---|
| `start`, `end` | Required: a date or a date and time. `end` can't be before `start`, or in the future. |
| `compress` | `no` (the default) or `gz` |

	curl "http://localhost:8000/replication/diff?start=2026-01-31T00:00:00&end=2026-01-31T06:00:00&compress=gz" -o diff.osc.gz

A time is taken as UTC unless it gives an offset, and a date means midnight UTC. In a URL, write the `+` of an offset as `%2B`. Otherwise it reads as a space, and the time gets `400`.

### `GET /replication/now`

The server's time, as JSON.

	{"now": "2026-01-31T12:00:00.123Z", "time": 1769860800.123}

### Periodic diffs

`/replication/{minute,hour,day}/...` follow the layout of [planet.openstreetmap.org's replication](https://wiki.openstreetmap.org/wiki/Planet.osm/diffs), as `.osc.gz` and `.state.txt` files, with two differences:

* Each diff is also available uncompressed, as `.osc`.
* There's no `state.txt` at the top of each of these folders, where replication clients such as Osmosis usually look for the latest diff.

Downloads
---------

### `GET /replication/extract/...`

A stored extract, by its ID or its unique name. The snapshot stored is streamed as it is: it's neither updated nor queried afresh.

| Path | Format |
|---|---|
| `/replication/extract/{id}` | OSM XML |
| `/replication/extract/{id}.osm.gz` | Gzipped OSM XML |
| `/replication/extract/{id}.o5m.gz` | Gzipped o5m |
| `/replication/extract/{id}.pbf` | PBF |
| `/replication/extract?name={name}` | OSM XML only |

An extract that doesn't exist gets `404`, and an ambiguous name gets `400`. The header of the `osm`, `osm.gz` and `o5m.gz` downloads records the edit the file is current to, and where to update it from.

See the [README](README.md#download-a-stored-extract) for more. Planet dumps aren't served by a view: they're files under `PLANET_DUMP_URL` (`/static/planet/` by default), listed at `/replication/planet`.

Statistics
----------

### `GET /extra/most_active`

The users who have made the most edits since a time, with how many objects of each type.

| Argument | Meaning |
|---|---|
| `start` | Required: a time within the last 31 days. It must give its offset (such as `Z`). Without one, or if it can't be read, the answer is `500`, not `400`. |

	<activeusers><user uid="1" username="someone" nodes="10" ways="2" relations="0"/></activeusers>

Overpass and XAPI
-----------------

These are the [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API)'s and [XAPI](https://wiki.openstreetmap.org/wiki/Xapi)'s interfaces, not the main API's. pycrocosm answers a subset of each.

### `GET`, `POST /api/interpreter`

An Overpass QL query. It's also at `/overpass/api/interpreter`.

| Argument | Meaning |
|---|---|
| `data` | The query. A POST can instead send it as the request's body. |
| `bbox` | West, south, east, north, for a query using `[bbox]` |

	curl -d 'data=[out:json];nwr[amenity=pub](50.78,-1.10,50.80,-1.05);out center;' http://localhost:8000/api/interpreter

Any site may call it (`Access-Control-Allow-Origin: *`). Each client address may make `OVERPASS_RATE_LIMIT_REQUESTS` queries in `OVERPASS_RATE_LIMIT_WINDOW_SECONDS` (60 in 60 seconds by default), after which the answer is `429`. Queries that fail are answered with `Error: ...` as plain text. [overpass/README.md](overpass/README.md) sets out what the language covers, how it differs from the main Overpass servers, and its limits.

### `GET /overpass/xapi/{query}`, `GET /overpass/xapi_meta?{query}`

An XAPI query, as part of the path or as the whole query string. It starts with `node`, `way`, `relation` or `*`, followed by filters in brackets.

| Filter | Meaning |
|---|---|
| `[bbox=west,south,east,north]` | Within this area |
| `[key=value]` | Has this tag |
| `[key=*]` | Has this key |

At least one filter is required. The answer is OSM XML, with what's needed to draw the objects found.

	curl -g "http://localhost:8000/overpass/xapi/way[highway=primary][bbox=-1.1,50.78,-1.05,50.8]"

OAuth2
------

OAuth2 itself is standard, at `/oauth2/authorize` and `/oauth2/token`: see the [README](README.md#oauth2). These are added for clients to find it.

### `GET /.well-known/oauth-authorization-server`

The server's [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414) metadata: its endpoints, scopes and supported methods, as JSON.

### `/api/authorize`, `/api/token`

The same as `/oauth2/authorize` and `/oauth2/token`, next to the API, where JOSM looks when it can't use discovery.

Differences on standard paths
-----------------------------

These paths are standard, but don't behave as they do on openstreetmap.org.

| Path | Here |
|---|---|
| `/api/0.6/notes...`, `/api/0.6/gpx...`, `/api/0.6/trackpoints`, `/api/0.6/user/gpx_files` | `501 Not implemented`, for GET and POST. `capabilities` still advertises limits for notes, as editors expect to find them. |
| `POST /api/0.6/changeset/{id}/expand_bbox` | `410 Gone`. It's been removed from the standard API too. |
| `/oauth2/application/{client_id}` | A web page for one of your applications, where openstreetmap.org has `/oauth2/applications/{id}` |

`/api` and `/api/` answer with the text `API root`.

Web pages
---------

These are for people rather than programs. They're listed so that the routes are all accounted for.

| Path | Page | App |
|---|---|---|
| `/` | Front page | `frontpage` |
| `/history` | Recent changesets | `objectinfo` |
| `/changeset/{id}` | One changeset | `objectinfo` |
| `/register/` | Sign up | `register` |
| `/accounts/...` | Log in and manage a password | Django |
| `/admin/` | Admin panel | Django |
| `/replication/` | Links to replication data and downloads | `replicate` |
| `/replication/extracts` | Stored extracts, with download links | `replicate` |
| `/replication/planet` | Planet dumps, with download links | `replicate` |
| `/oauth2/applications`, `/oauth2/authorized_applications` | Manage applications, and those you've authorised. Login needed. | `oauth2` |

pycromap
--------

pycromap is a separate Django project in [pycromap/](pycromap/), with its own server (port 8082 under its Docker setup). It draws the map from pycrocosm's data, and none of its endpoints needs a login. All of them are GET only.

### `GET /tiles/{z}/{x}/{y}.pbf`

A vector tile, in the OpenMapTiles schema. A tile that isn't ready is rendered while the request waits, for up to `TILE_REQUEST_WAIT` seconds (600 by default). If the client goes away, the render is cancelled.

| Status | Meaning |
|---|---|
| `200` | The tile, gzipped (`Content-Encoding: gzip`), for a zoom that isn't cached |
| `302` | A redirect to the tile's file, for a cached zoom. The file can be cached, but the redirect can't, as each rendering has its own file. |
| `204` | Nothing to draw. With `X-Tile-Status: placeholder`, the tile's summaries won't be built soon, so ask again later. |
| `404` | That zoom isn't served, or the tile isn't on the grid |
| `503` | The tile couldn't be rendered, or isn't ready yet. Try again after `Retry-After`. |

### Other endpoints

| Path | Gives |
|---|---|
| `/metadata` | JSON: where the map page opens (`bounds`), and the zoom limits |
| `/spec.json` | [TileJSON](https://github.com/mapbox/tilejson-spec) for the tiles. Its tile URL starts at the root of the server. |
| `/style.json` | The [MapLibre style](https://maplibre.org/maplibre-style-spec/), pointed at `/spec.json` |
| `/tilecache/{path}` | A cached tile's file. It's served by Django only while `DEBUG` is on: in production, the web server does this. |

`/metadata`, `/spec.json` and `/style.json` are sent with `Cache-Control: no-store`.

### The map page

`/` is the map itself. These arguments change only how it's drawn, so they cause no rendering.

| Argument | Meaning |
|---|---|
| `lang` | The main label's language: a code such as `de` or `zh-Hans`, or `local` for the object's own name (the default) |
| `lang2` | The second label's language, in the same form. `en` by default. |
| `prow` | `1` draws UK public rights of way in colours of their own |
| `projection` | `mercator` (the default), `globe` or `vertical-perspective` |

The view is kept in the address as `#zoom/lat/lon`.
