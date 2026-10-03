[![CircleCI](https://circleci.com/gh/TimSC/pycrocosm.svg?style=svg)](https://circleci.com/gh/TimSC/pycrocosm)

# pycrocosm

OSM Map server API 0.6 implemented using Django and Python 3. It depends on submodule https://github.com/TimSC/pgmap to handle the PostGIS database.

Installation
------------

pycrocosm runs in Docker, connecting to a PostgreSQL/PostGIS server on the host machine. Install Docker (with the compose plugin) and PostgreSQL, then clone the repository with its submodules:

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

and in `/etc/postgresql/<version>/main/pg_hba.conf` allow the Docker address range:

    host    all    all    172.16.0.0/12    scram-sha-256

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

Create the Django specific tables:

    docker compose run --rm web python3 manage.py migrate

Optionally, create an admin user:

    docker compose run --rm web python3 manage.py createsuperuser

Start the server:

    docker compose up

Connect to http://127.0.0.1:8000/ using a web browser and hope for the best.

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

Run unit tests
--------------

     docker compose run --rm web python3 manage.py test

Highly loaded servers
---------------------

Highly loaded Linux servers should consider increasing net.core.somaxconn to about 1024. This prevents errors such as "(11: Resource temporarily unavailable) while connecting to upstream". https://blog.narrativ.com/uwsgi-and-nginx-connection-queues-43eaba95047e Running scripts that repeatedly and rapidly access the API can trigger this problem.

Add net.core.somaxconn=1024 to /etc/sysctl.conf for it to become permanent, then reboot. https://serverfault.com/a/271386/375337

All done!

