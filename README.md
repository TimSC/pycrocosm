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

Also set `DEBUG=0` in `.env`. gunicorn does not serve static files, so put a reverse proxy in front of it to handle `/static/` and TLS, forwarding other requests to port 8000. Two are catered for:

* nginx: see [nginx/README.md](nginx/README.md), which sets it up with uwsgi under systemd.
* Caddy, which obtains and renews its HTTPS certificates by itself: see [caddy/README.md](caddy/README.md), a ready-made Docker Compose setup.

Read only mode
--------------

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

The file takes its name only once it is complete, so a dump being written is never seen half finished, and an existing dump is never replaced. Except in .pbf, the header records the edit activity ID and atomic edit ID the dump is current to. The dumps are listed, newest first with their sizes, on a public page at `/replication/planet`, which the front page links to. Each entry links to the file under `PLANET_DUMP_URL`, the address at which the web server offers the dump folder: `/static/planet/` by default, which the development server serves. A dump still being written is not listed.

Updating database extracts
--------------------------

Extracts stored in the map database (see the admin pages, and pgmap's README) are brought up to date with the map by:

	python3 manage.py updateextracts

Without options it updates only the extracts enabled for automatic updates; `--all` updates every extract and `--id N` (which may be repeated) updates particular ones, both whatever their setting. Each extract is updated in its own transaction, so one that fails does not hold back the others; the command then finishes with an error status. An extract set to update from another API is skipped, because that is not implemented yet.

Overpass queries
----------------

The server answers a subset of [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL) at `/api/interpreter`, and the older XAPI requests under `/overpass/xapi/`. What it understands, how it differs from the main Overpass servers and the settings that limit it are described in [overpass/README.md](overpass/README.md).

Highly loaded servers
---------------------

Highly loaded Linux servers should consider increasing net.core.somaxconn to about 1024. This prevents errors such as "(11: Resource temporarily unavailable) while connecting to upstream". https://blog.narrativ.com/uwsgi-and-nginx-connection-queues-43eaba95047e Running scripts that repeatedly and rapidly access the API can trigger this problem.

Add net.core.somaxconn=1024 to /etc/sysctl.conf for it to become permanent, then reboot. https://serverfault.com/a/271386/375337

All done!
