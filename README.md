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

Settings are read from environment variables in `.env`. Create it from the template and edit it:

    cp env.template .env

    nano .env

Set `DJANGO_DB_HOST=host.docker.internal` and the database user and password. If you want to access the site from other computers, `DJANGO_ALLOWED_HOSTS` needs to be set as well. In production, set `DEBUG=0` and generate a new `SECRET_KEY`.

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

Download a complete database extract as OSM XML using either its ID or its unique name:

     curl -o extract.osm http://localhost:8000/replication/extract/1
     curl -G --data-urlencode 'name=my-extract' -o extract.osm http://localhost:8000/replication/extract

The endpoint streams the stored snapshot in batches; it does not update the extract
or repeat its bbox query. Missing extracts return 404; ambiguous names or invalid
selectors return 400. The snapshot transaction remains open during streaming and
closes when the download finishes or the response is closed.

Run unit tests
--------------

     docker compose run --rm web python3 manage.py test

Run the stored-extract tests alone (rebuild the image if the pgmap bindings have changed):

     docker compose run --rm web python3 manage.py test querymap.test_dbextract

These tests create nodes through the upload path, save and update database extracts,
and compare their exports with fresh bbox queries. They use unique temporary table
prefixes in `MAP_DATABASE` and remove them afterward, without resetting the map's
existing tables. This targeted suite does not need a Django test database.

The tests create a temporary `test_db_settings` database, so the pycrocosm user needs permission to create databases:

     sudo -u postgres psql -c "ALTER USER pycrocosm CREATEDB;"

Highly loaded servers
---------------------

Highly loaded Linux servers should consider increasing net.core.somaxconn to about 1024. This prevents errors such as "(11: Resource temporarily unavailable) while connecting to upstream". https://blog.narrativ.com/uwsgi-and-nginx-connection-queues-43eaba95047e Running scripts that repeatedly and rapidly access the API can trigger this problem.

Add net.core.somaxconn=1024 to /etc/sysctl.conf for it to become permanent, then reboot. https://serverfault.com/a/271386/375337

All done!
