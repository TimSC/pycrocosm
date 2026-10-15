[![CircleCI](https://circleci.com/gh/TimSC/pycrocosm.svg?style=svg)](https://circleci.com/gh/TimSC/pycrocosm)

# pycrocosm

OSM Map server API 0.6 implemented using Django and Python 3. It depends on submodule https://github.com/TimSC/pgmap to handle the PostGIS database.

Installation
------------

pycrocosm runs in Docker, connecting to a PostgreSQL/PostGIS server on the host machine. Install Docker (with the compose plugin) https://docs.docker.com/engine/install/ubuntu/ and PostgreSQL, then clone the repository with its submodules:

    git clone --recursive https://github.com/TimSC/pycrocosm.git

    cd pycrocosm

### Map database

You need to configure and initialize the PostGIS map database using the tools included in https://github.com/TimSC/pgmap, mainly admin. Follow the steps at: https://github.com/TimSC/osm2pgcopy/blob/master/README.md to initialize the map database (named `db_map` by default) and import some data.

### Django settings database

Create a database to contain Django specific tables:

    sudo -u postgres psql

    CREATE DATABASE db_settings;

    GRANT ALL PRIVILEGES ON DATABASE db_settings to pycrocosm;

Use Ctrl-D to exit.

### Allow connections from Docker containers

The container reaches PostgreSQL on the host via `host.docker.internal`. By default PostgreSQL only listens on localhost, so in `/etc/postgresql/<version>/main/postgresql.conf` set:

    listen_addresses = '*'

and in `/etc/postgresql/<version>/main/pg_hba.conf` allow the pycrocosm user to connect from the Docker address range:

    host    all    pycrocosm    172.16.0.0/12    scram-sha-256

The database column needs to be `all` (rather than just `db_settings` and `db_map`) because running the unit tests also connects to `test_db_settings` and `postgres`.

Then restart PostgreSQL (a reload is not enough for `listen_addresses`):

    sudo systemctl restart postgresql

If you use a firewall such as ufw, also allow port 5432 from Docker:

    sudo ufw allow from 172.16.0.0/12 to any port 5432

### Configuration

The map database's name, login and table prefixes are set once, in `pgmap/config.cfg`, where pgmap's command line tools read them. `MAP_DATABASE` in `pycrocosm/settings.py` fetches them from that file through the pgmap module (`pgmap.GetConfigValue(name, default)`), so they are not repeated there. Set `PGMAP_CONFIG` in the environment to use a file somewhere else. Any of pgmap's settings can also be given in the environment, as its name in capitals after `PGMAP_` (`PGMAP_DBHOST` for `dbhost`, `PGMAP_DBPASS` for `dbpass`), which replaces what the file says and works without a file at all; pgmap's command line tools read these too.

The Django settings database takes nothing from `config.cfg`: it is set by the `DJANGO_DB_*` environment variables. Those and the other settings are read from `.env`, whose values also take precedence over `config.cfg` for the map database. Create it from the template and edit it:

    cp env.template .env

    nano .env

Set `DJANGO_DB_HOST=host.docker.internal` and the database user and password. Every database setting in `pycrocosm/settings.py` can be replaced this way: the name, user, password, host and port of the settings database (`DJANGO_DB_*`), and the same for the map database plus its table prefixes (`DJANGO_MAP_DB_*`, which fall back to the `DJANGO_DB_*` values for user, password, host and port). `env.template` lists them all; anything left unset keeps the value in `settings.py`. If you want to access the site from other computers, add their names for it to `DJANGO_ALLOWED_HOSTS`, a comma separated list. In production, set `DEBUG=0` and generate a new `SECRET_KEY`.

### Build and run

Build the image (this also compiles pgmap):

    docker compose build

pgmap is compiled with `-g0 -O1` by default, which builds faster and smaller without noticeably affecting performance. To change the compiler flags, for example to include debug information when debugging pgmap, pass the `PGMAP_CFLAGS` build argument:

    docker compose build --build-arg PGMAP_CFLAGS="-g -O0"

An empty value (`PGMAP_CFLAGS=""`) uses Python's default flags (`-g -O2`). Outside Docker, set `PGMAP_CFLAGS` as an environment variable when running `pip install`.

Create the Django specific tables:

    docker compose run --rm web python3 manage.py migrate

Optionally, create an admin user, who can log in to the admin panel (see below):

    docker compose run --rm web python3 manage.py createsuperuser

Start the server:

    docker compose up

Connect to http://127.0.0.1:8000/ using a web browser and hope for the best.

### Production server

The default `docker-compose.yml` uses Django's `runserver`, which is only intended for development: it is single process, auto-reloads on file changes and has not been security audited. A production server really should use gunicorn (already in `requirements.txt`) or a similar WSGI server instead. To do this in Docker, create a `docker-compose.prod.yml` that overrides the command:

    services:
      web:
        command: gunicorn pycrocosm.wsgi:application --bind 0.0.0.0:8000 -w 2 --timeout 300
        restart: unless-stopped

Increase `-w` (worker processes) to suit the number of CPU cores. Then start it with:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

Also set `DEBUG=0` in `.env`. gunicorn does not serve static files, so put a reverse proxy in front of it to handle `/static/` and TLS, forwarding other requests to port 8000. Two are catered for:

* nginx: see [nginx/README.md](nginx/README.md), which sets it up with uwsgi under systemd.
* Caddy, which obtains and renews its HTTPS certificates by itself: see [caddy/README.md](caddy/README.md), a ready-made Docker Compose setup.

Admin panel
-----------

The admin panel is at `/admin/`: http://127.0.0.1:8000/admin/ on a local install. Log in with an account that has staff status, such as the one made by `createsuperuser` above. Besides Django's own pages for users and groups, it has:

* **Database extracts**: the extracts stored in the map database. List them, see one's details and object counts, download it, save a new one, bring one up to date, delete one, and set whether it is updated automatically.
* **Map parameters**: the settings pgmap keeps in the map database, such as `useBboxInQuery` and `readonly`, which can be changed there.
* **OAuth2 applications and authorizations**, and **user preferences**.

Staff accounts can look at the extracts and map parameters; changing them needs a superuser or the matching permission. After upgrading, run `manage.py migrate` so that new admin pages and their permissions are registered.

Map call formats
----------------

`/api/0.6/map?bbox=left,bottom,right,top` answers in XML, or in JSON when asked as `map.json` or with an `Accept: application/json` header. A `format` argument chooses among more: `osm`, `json`, `o5m` and `pbf`, and `osm.gz`, `json.gz` and `o5m.gz` for a gzipped file, as in

     curl -o map.pbf "http://127.0.0.1:8000/api/0.6/map?bbox=-1.09,50.79,-1.06,50.81&format=pbf"

The binary formats are several times smaller than XML: for the same area, around a twentieth of its size as `pbf` or `o5m.gz`.

Read only mode
--------------

Set server to read only mode: 

     docker compose run --rm web python3 manage.py setmeta readonly 1

Download a stored extract
-------------------------

The stored extracts are listed publicly, with download links, at
http://localhost:8000/replication/extracts and that page is linked from the replication page, http://localhost:8000/replication/, which the front page links to.

Download a complete database extract as OSM XML using either its ID or its unique name:

     curl -o extract.osm http://localhost:8000/replication/extract/1
     curl -G --data-urlencode 'name=my-extract' -o extract.osm http://localhost:8000/replication/extract

Add `.osm.gz` to the ID form for a gzipped download, which is what the admin page links to:

     curl -o extract.osm.gz http://localhost:8000/replication/extract/1.osm.gz

The endpoint streams the stored snapshot in batches; it does not update the extract
or repeat its bbox query. Missing extracts return 404; ambiguous names or invalid
selectors return 400. The snapshot transaction remains open during streaming and
closes when the download finishes or the response is closed.

Run unit tests
--------------

     docker compose run --rm web python3 manage.py test

Run the stored-extract tests alone (rebuild the image if the pgmap bindings have changed):

     docker compose run --rm web python3 manage.py test querymap.test_dbextract

These tests create and edit nodes, ways and relations through the upload path, save and update database extracts,
and compare their exports with fresh bbox queries. They use unique temporary table
prefixes in `MAP_DATABASE` and remove them afterward, without resetting the map's
existing tables. This targeted suite does not need a Django test database.

The tests create a temporary `test_db_settings` database, so the pycrocosm user needs permission to create databases:

     sudo -u postgres psql -c "ALTER USER pycrocosm CREATEDB;"

Scheduled tasks
---------------

Three jobs are meant to be run regularly, for instance by cron:

| Command | What it does | A sensible interval |
|---|---|---|
| `python3 manage.py closeoldchangesets` | Closes changesets left open for more than a day | Hourly |
| `python3 manage.py updateextracts` | Brings the stored database extracts enabled for automatic updates up to date with the map (see below) | Every few minutes to daily, as fresh as the extracts need to be |
| `python3 manage.py dumpplanet` | Writes a dump of the whole map (see below) | Monthly |

With the Docker setup, a crontab for them could be:

	5 * * * *    cd /path/to/pycrocosm && docker compose exec -T web python3 manage.py closeoldchangesets
	*/15 * * * * cd /path/to/pycrocosm && docker compose exec -T web python3 manage.py updateextracts
	30 2 1 * *   cd /path/to/pycrocosm && docker compose exec -T web python3 manage.py dumpplanet

None of these commands runs twice at once. Each takes a lock named after itself in the settings database when it starts; if an earlier run of the same command still holds it, the new one prints a line saying so and finishes straight away, successfully, without touching the map. So a schedule shorter than a job takes does no harm: the extra runs are skipped. The lock belongs to the running process, so a command that is killed or crashes leaves nothing to clear up, and different commands do not wait for each other.

Planet dumps
------------

A dump of the whole map is made by:

	python3 manage.py dumpplanet

It is written to `static/planet/`, which is created if need be, with a name such as `fosm-planet_20260501013701.o5m`: the time the dump was started, in UTC. `PLANET_DUMP_DIR` and `PLANET_DUMP_FILENAME` in `pycrocosm/settings.py` change the folder and the name. The name is a pattern in which strftime codes stand for the time, and its ending selects the format: .osm, .o5m, .pbf or .json, optionally followed by .gz. The `--dir` and `--filename` options do the same for one run.

The file takes its name only once it is complete, so a dump being written is never seen half finished, and an existing dump is never replaced. Except in .pbf, the header records the edit activity ID and atomic edit ID the dump is current to. The dumps are listed, newest first with their sizes, on a public page at `/replication/planet`, linked from the replication page at `/replication/`. Each entry links to the file under `PLANET_DUMP_URL`, the address at which the web server offers the dump folder: `/static/planet/` by default, which the development server serves. A dump still being written is not listed.

Database extracts from the command line
---------------------------------------

Everything the admin panel does with extracts stored in the map database can also be done with manage.py (prefix each with `docker compose run --rm web` with the Docker setup):

| Command | What it does |
|---|---|
| `python3 manage.py saveextract --bbox W,S,E,N --name harbour` | Saves a new extract of the map in a rectangle, or a polygon with `--wkt` or `--wkt-file` (see below), and prints its ID |
| `python3 manage.py updateextracts --id N` | Brings extracts up to date (see "Updating database extracts") |
| `python3 manage.py exportextract --id N --out harbour.osm.gz` | Writes an extract to a file; `--name` selects it by its name instead |
| `python3 manage.py importextract --in harbour.osm.gz --name harbour` | Stores a file as a new extract and prints its ID |
| `python3 manage.py compareextract --id N` | Compares an extract with a fresh map query of its area and lists the objects that differ; `--all` compares every extract |
| `python3 manage.py deleteextract --id N` | Deletes an extract, after you type `delete` to confirm, or straight away with `--yes` |

`exportextract` and `importextract` take the format from the file name: .osm, .o5m, .pbf or .json, optionally followed by .gz. The file's header records the edit activity ID and atomic edit ID the extract is current to, unless `--no-edit-ids` is given or the format is .pbf, which has no room for them, and also any polygon boundary. `importextract` reads them back, so a file written by `exportextract` comes back as an extract that can be kept up to date. Give `--edit-activity-id` and `--atomic-edit-id` together to set the checkpoint instead, and `--bbox`, `--wkt` or `--wkt-file` to set the area instead of the file's bounds; the contents are stored as they are and are not trimmed to the area. A new extract's name must not already be in use. Each command reports how long it took.

Extract boundaries
------------------

An extract covers a rectangle, or any polygon given as WKT, such as a town's boundary, or a multipolygon of several, such as a region with islands. To save one from the command line:

    docker compose run --rm web python3 manage.py saveextract --bbox -1.078,50.788,-1.074,50.790 --name harbour
    docker compose run --rm web python3 manage.py saveextract --wkt-file town.wkt --name town

(`--wkt` takes the shape itself in place of a file.) In the admin panel, fill in "Or a boundary (WKT)" in place of the four sides; `importextract` and pgmap's `extract --save-db` take `--wkt` too. Longitude comes before latitude, for example `POLYGON((-1.08 50.78, -1.06 50.78, -1.07 50.80, -1.08 50.78))`, or `MULTIPOLYGON(((...)), ((...)))` for several pieces. Only a valid POLYGON or MULTIPOLYGON is taken: other kinds of geometry, a self-crossing ring, or a shape crossing the antimeridian (anything wider than 180 degrees of longitude) are refused with the reason. Multipolygon boundaries need schema version 15; going back to 14 is refused while any extract has one.

To take in a margin around a boundary, add `--buffer` with a distance in metres, to `saveextract`, `importextract` or pgmap's `extract --wkt`, or fill in "Buffer (metres)" in the admin panel:

    docker compose run --rm web python3 manage.py saveextract --wkt-file town.wkt --buffer 2000 --name town-and-around

The boundary is grown outwards by that distance on the ground (with PostGIS's `ST_Buffer` on geography) before it is checked and stored, and the extract's boundary is then the grown shape, as if it had been given that way: that is what the extracts page, downloads and updates use. Pieces of a multipolygon closer together than twice the distance join into one. A buffer goes with a WKT boundary only, not a rectangle, and is refused if the grown shape would cross the antimeridian.

The extract holds what is inside the shape, by the same rules as a rectangle, and is kept up to date and compared with the map the same way: a way with a node in any piece is in it, with all its nodes. Files downloaded from it carry the rectangle around the shape as their bounds, which is all those formats have room for, and the shape itself as a `boundary` attribute of the file (in .osm, .o5m and .json, not .pbf), so that importing the file again gives the same extract. The extracts page shows a polygon as "polygon within" its rectangle, and a multipolygon as "multipolygon within" it.

Updating database extracts
--------------------------

Extracts stored in the map database (see the admin pages, and pgmap's README) are brought up to date with the map by:

	python3 manage.py updateextracts

Without options it updates only the extracts enabled for automatic updates; `--all` updates every extract and `--id N` (which may be repeated) updates particular ones, both whatever their setting. Each extract is updated separately, so one that fails does not hold back the others; the command then finishes with an error status.

### Extracts that follow another server

An extract can follow the map of another pycrocosm server instead of this one. Give it that server's address as its "Update from" setting in the admin panel (the address of its front page, such as `https://maps.example.org/`). Updating it then uses only the other server's web API, and never reads this server's map:

* `replication/edit_ids` says how far the other server's edits have got;
* `replication/edit_activities` gives the edits since the extract's checkpoint, each with the objects it changed and those around them. One answer holds at most `EDIT_ACTIVITY_MAXIMUM_RECORDS` records (100 unless set otherwise); an answer that was cut short says so, with `truncated="true"` and the `next_id` to continue from. Asked without a range of IDs or times it gives the latest records, 20 unless `limit` (up to the maximum) or `EDIT_ACTIVITY_DEFAULT_RECORDS` says otherwise. `bbox=minlon,minlat,maxlon,maxlat` keeps only the records that may concern that area. An extract with an update URL asks this way for its own rectangle, unless `EXTRACT_UPDATE_USE_BBOX` is set to False. An extract of this server's own map is brought up to date the same way, from the edits recorded since its checkpoint that concern its rectangle, so the time taken depends on those edits and not on the size of the extract. The whole extract is taken from the map afresh instead, which takes time in proportion to its size, when it selects ways and relations by their stored bounding boxes (saved while `useBboxInQuery` was on), when the recorded edits predate what an update needs, or when `EXTRACT_UPDATE_FROM_RECORDS` is set to False.

Those records carry everything an update needs, so nothing else is asked of the other server. Edits recorded before a server kept the relations that hold an edit's related nodes (its `related_parents` column, added to schema 14 later) do not: an update that reaches one is refused, and the extract has to be saved or imported afresh from after them.

The extract's contents and checkpoint have to come from that server to begin with. Download one of its extracts (the header of the .osm.gz and .o5m.gz downloads records the checkpoint) and bring it in with `python3 manage.py importextract`, then set the address.

Such an extract always holds what a map query without stored bounding boxes selects: the nodes in its rectangle, the ways with such a node and all their nodes, and the relations with one of those nodes or ways as a member.

Overpass queries
----------------

The server answers a subset of [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL) at `/api/interpreter`, and the older XAPI requests under `/overpass/xapi/`. What it understands, how it differs from the main Overpass servers and the settings that limit it are described in [overpass/README.md](overpass/README.md).

Bounding boxes and search indices
---------------------------------

Out of the box, the map finds the ways and relations in an area through their nodes: a way is in the area if one of its nodes is. Every way and relation can also have its bounding box stored with it, and with that and two optional sets of indices, searches by area and by tag can go straight to what they want. Three steps in pgmap's admin tool and one setting in the admin panel turn this on.

### 1. "Update way/relation bboxes" (admin tool, option e)

Works out and stores the box of every way and relation. Edits keep the boxes of what they touch up to date from then on, so this is needed once after an import, and again only if the boxes have been lost. Run "Create indicies" first.

On a whole planet this is a long step, as every way has to find its nodes, and it can take days. The ways are done 50,000 at a time and each batch is kept as it finishes, so the tool shows how far it has got and loses nothing if stopped: run it again and answer 1 to "Only ways that have no box yet" to carry on from where it was. The relations are worked out at the end. Put the server in read only mode while it runs.

### 2. "Create/drop bbox indices" (admin tool, option i)

Builds two kinds of index:

* a spatial index on the stored box of each way and relation, without which finding them by box means reading them all, and
* a text index on the tags of every node, way and relation, which the tag filters of Overpass and XAPI queries (`[k]`, `[k=v]`) use. These help whether or not boxes are used.

A search for a tag within an area uses both. On a planet the text index takes a long time to build, as it reads the tags of every object; a generous `maintenance_work_mem` helps (see pgmap's README). The same option drops them again. Tables that have the single combined index earlier versions built keep it; drop and create to change over.

### 3. Setting `useBboxInQuery`

In the admin panel, under "Map parameters", set `useBboxInQuery` to 1. If some ways still have no box, the page says how many (an estimate on a large map), as those ways would no longer be found by area. Setting it back to 0 returns to finding ways and relations through their nodes, at once.

### What it changes

With `useBboxInQuery` at 1:

* **Overpass and XAPI queries** find the ways and relations in a bounding box with one indexed search on their boxes, where otherwise they find every node in the area and then what holds those nodes. A query such as `way[highway](bbox)` becomes a single lookup. The details are in [overpass/README.md](overpass/README.md).
* **The map call** (`/api/0.6/map`) selects ways and relations whose boxes overlap the area. This also returns ways that cross the area without a node inside it, which the node rule misses, so the answer can be a little larger than before.
* **Stored extracts saved while it is on** follow the same rule. They are brought up to date by being taken from the map afresh, which takes time in proportion to their size, as the recorded edits cannot keep up a selection made by boxes.

Without the boxes being worked out first, step 1, ways and relations with no box are simply not found by area, which is why the admin panel warns. The boxes are kept up to date by edits whatever the setting, and the edit activity records take their boxes from them, so step 1 is worth doing on any map whose edits other servers follow to keep their extracts up to date: without them, an edit to a relation that holds other relations is recorded as possibly concerning anywhere, and is sent to every extract.

Running in production
---------------------

[PRODUCTION.md](PRODUCTION.md) lists the settings to change for a public server, the recommended reverse proxy and application server, and PostgreSQL settings for fast queries.

All done!
