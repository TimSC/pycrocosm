[![CircleCI](https://circleci.com/gh/TimSC/pycrocosm.svg?style=svg)](https://circleci.com/gh/TimSC/pycrocosm)

# pycrocosm

OSM Map server API 0.6 implemented using Django and Python 3. It depends on submodule https://github.com/TimSC/pgmap to handle the PostGIS database.

Installation
------------

pycrocosm runs in Docker, connecting to a PostgreSQL/PostGIS server on the host machine. Install Docker (with the compose plugin) https://docs.docker.com/engine/install/ubuntu/ and PostgreSQL, then clone the repository with its submodules:

    git clone --recursive https://github.com/TimSC/pycrocosm.git

    cd pycrocosm

### Map database

You need to configure and initialize the PostGIS map database using the tools included in https://github.com/TimSC/pgmap, mainly osm2csv and admin. Follow the steps at: https://github.com/TimSC/osm2pgcopy/blob/master/README.md to initialize the map database (named `db_map` by default) and import some data.

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

The map database's name, login and table prefixes are set once, in `pgmap/config.cfg`, where pgmap's command line tools read them. `MAP_DATABASE` in `pycrocosm/settings.py` fetches them from that file through the pgmap module (`pgmap.GetConfigValue(name, default)`), so they are not repeated there. Set `PGMAP_CONFIG` in the environment to use a file somewhere else.

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

Optionally, create an admin user:

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

Also set `DEBUG=0` in `.env`. gunicorn does not serve static files, so put a reverse proxy such as nginx in front of it to handle `/static/` and TLS, forwarding other requests with `proxy_pass http://127.0.0.1:8000;`.

nginx configuration
-------------------

For nginx/systemd based linux:

    sudo apt install nginx uwsgi uwsgi-plugin-python

    sudo cp /var/pycrocosm/nginx/pycrocosm /etc/nginx/sites-available

    sudo ln -s /etc/nginx/sites-available/pycrocosm /etc/nginx/sites-enabled/pycrocosm

    sudo service nginx restart

This gets nginx listen on socket /run/pycrocosm.sock for a wsgi server. Then update /var/pycrocosm/pycrocosm.ini with your install and virtualenv path.

    sudo cp nginx/pycrocosm.service /etc/systemd/system

Check things look ok in pycrocosm.service:

    sudo nano /etc/systemd/system/pycrocosm.service

Start the service:

    sudo service pycrocosm start

Check it is running:

    sudo service pycrocosm status

If not, check the logs:

    sudo journalctl -u pycrocosm.service

Enable the service to start on boot:

    sudo systemctl enable mysite.service

Connect using a browser: http://localhost:8010

TODO It might be safer to not run the service as root!

Set server to read only mode: 

     docker compose run --rm web python3 manage.py setmeta readonly 1

Download a stored extract
-------------------------

The stored extracts are listed publicly, with download links, at
http://localhost:8000/replication/extracts and that page is linked from the front page.

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

Updating database extracts
--------------------------

Extracts stored in the map database (see the admin pages, and pgmap's README) are brought up to date with the map by:

	python3 manage.py updateextracts

Run it from a scheduler such as cron to keep them current. Without options it updates only the extracts enabled for automatic updates; `--all` updates every extract and `--id N` (which may be repeated) updates particular ones, both whatever their setting. Each extract is updated in its own transaction, so one that fails does not hold back the others; the command then finishes with an error status. An extract set to update from another API is skipped, because that is not implemented yet.

Overpass queries
----------------

The server answers a subset of [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL) at `/api/interpreter` (also `/overpass/api/interpreter`). Send the query as the `data` parameter of a GET or POST, or as the body of a POST:

	curl -d 'data=[out:json];nwr[amenity=pub](50.78,-1.10,50.80,-1.05);out center;' http://localhost:8000/api/interpreter

To use [Overpass Turbo](https://overpass-turbo.eu/), set its server to the address ending in `/api/`.

What is understood:

* Settings: `[out:xml]`, `[out:json]` or `[out:csv(...)]`, `[timeout:]`, `[bbox:south,west,north,east]`, and `[bbox]` with a separate `bbox=west,south,east,north` parameter. `[maxsize:]` is accepted and ignored.
* Queries for `node`, `way`, `rel`/`relation`, `nwr`, `nw`, `nr` and `wr`, with these filters:
    * tags: `[k]`, `[!k]`, `[k=v]`, `[k!=v]`, `[k~regex]` and `[k!~regex]` (add `,i` to ignore case);
    * place: a bounding box `(south,west,north,east)`, or `(around:metres,lat,lon)`, `(around:metres)` and `(around.set:metres)` for a distance from a position or from the elements of a set;
    * identity: `(id)` or `(id:a,b)`, and input sets `.name`;
    * links to a set: `(w)`, `(r)`, `(bn)`, `(bw)` and `(br)`, optionally with a set and a role as in `(r.routes:"stop")`;
    * last edit: `(uid:1,2)`, `(user:"name")`, `(newer:"2026-01-01T00:00:00Z")` and `(changed:"from","to")`.
* Named sets with `->.name`, unions `( ...; )`, differences `( a; - b; )`, a set as a statement `.name;`, and the recursion statements `>`, `>>`, `<` and `<<`.
* `out` with `ids`, `skel`, `body`, `tags` or `meta`; `geom`, `bb` or `center`; `count`; and a maximum number of elements. `qt` is accepted and ignored: output is in ID order.
* CSV output takes tag keys and the special fields `::id`, `::type`, `::otype`, `::lat`, `::lon`, `::version`, `::timestamp`, `::changeset`, `::uid`, `::user`, `::count`, `::count:nodes`, `::count:ways` and `::count:relations`, then optionally `;false` for no header line and a quoted separator in place of the tab.

Everything else in the language, such as areas, `foreach`, `if:` filters and `make`, is refused with a message naming the feature. Overpass XML queries are not read.

Differences from the main Overpass servers worth knowing:

* A way or relation is in a bounding box, or within a distance, if one of its nodes is (for a relation, a node of a member way counts). A way that only crosses the box, or passes near a position between two of its nodes, is not found. Likewise `around` a way or relation measures from its nodes.
* On a map whose `useBboxInQuery` setting is on, ways and relations are instead found in a bounding box by their own stored boxes: one indexed search, which also finds whatever has a box overlapping the area, including ways that cross it. Distances are still measured from nodes.
* A query must have something to search by: a bounding box or circles covering no more than `OVERPASS_AREA_MAXIMUM`, an ID, a set, or a filter of the form `[k]` or `[k=v]`. The other tag filters and the last edit filters alone would read the whole map and are refused.
* Finding every way in a bounding box takes about as long as downloading that area from `/api/0.6/map`. Queries with a `[k]` or `[k=v]` filter are much quicker.
* `(user:"name")` finds edits stored under that name, and edits by this server's account of that name. An `around` filter measures from at most 5000 positions.

The limits are set by `OVERPASS_TIMEOUT_DEFAULT`, `OVERPASS_TIMEOUT_MAXIMUM`, `OVERPASS_ELEMENTS_MAXIMUM`, `OVERPASS_RATE_LIMIT_REQUESTS` and `OVERPASS_RATE_LIMIT_WINDOW_SECONDS`, described in `settings.py.template`. The older XAPI calls under `/overpass/xapi/` run on the same code.

Highly loaded servers
---------------------

Highly loaded Linux servers should consider increasing net.core.somaxconn to about 1024. This prevents errors such as "(11: Resource temporarily unavailable) while connecting to upstream". https://blog.narrativ.com/uwsgi-and-nginx-connection-queues-43eaba95047e Running scripts that repeatedly and rapidly access the API can trigger this problem.

Add net.core.somaxconn=1024 to /etc/sysctl.conf for it to become permanent, then reboot. https://serverfault.com/a/271386/375337

All done!
