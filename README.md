[![CircleCI](https://circleci.com/gh/TimSC/pycrocosm.svg?style=svg)](https://circleci.com/gh/TimSC/pycrocosm)

# pycrocosm

OSM Map server API 0.6 implemented using Django. It depends on submodule https://github.com/TimSC/pgmap to handle the PostGIS database.

Installation
------------

### Python 2 installation

Installation is described for Linux Mint 18.2, but should work on similar systems like Debian, Ubuntu Xenial or later. 

    cd /var

    sudo apt install git virtualenv python-pip swig3 g++ python-dev libpqxx-dev rapidjson-dev libexpat1-dev libboost-filesystem-dev

    sudo git clone --recursive https://github.com/TimSC/pycrocosm.git

    sudo chown www-data:www-data -R pycrocosm

    sudo chmod g+rwx -R pycrocosm

    cd pycrocosm

    virtualenv --python=/usr/bin/python pgmapenv

    source pgmapenv/bin/activate

Install the rest of the dependencies

    pip install -r requirements.txt

    cd pycrocosm/pgmap/

    make

    pip install .

    cd ..

At this stage, you need to configure and initialize the PostGIS database using the tools included in https://github.com/TimSC/pgmap, mainly osm2csv and admin. Follow the steps at: https://github.com/TimSC/osm2pgcopy/blob/master/README.md to initialize the map database and import some data.

### Python 3 Installation

Installation is described for Linux Mint 18.2, but should work on similar systems like Debian, Ubuntu Xenial or later. 

    cd /var

    sudo apt install git virtualenv python3-pip swig3 g++ python3-dev libpqxx-dev rapidjson-dev libexpat1-dev libboost-filesystem-dev

    sudo git clone --recursive https://github.com/TimSC/pycrocosm.git

    sudo chown www-data:www-data -R pycrocosm

    sudo chmod g+rwx -R pycrocosm

    cd pycrocosm

    virtualenv --python=/usr/bin/python3 pgmapenv3

    source pgmapenv3/bin/activate

Install the rest of the dependencies

    pip3 install -r requirements.txt

    cd pycrocosm/pgmap/

    make

    pip3 install .

    cd ..

At this stage, you need to configure and initialize the PostGIS database using the tools included in https://github.com/TimSC/pgmap, mainly osm2csv and admin. Follow the steps at: https://github.com/TimSC/osm2pgcopy/blob/master/README.md to initialize the map database and import some data.

Finishing Django site install
-----------------------------

Create a database to contain Django specific tables:

    sudo su postgres

    psql

    CREATE DATABASE db_settings;

    GRANT ALL PRIVILEGES ON DATABASE db_settings to pycrocosm;

Use Ctrl-D (repeatedly) to exit back to your normal user. Django needs to know the actual database settings. Set the appropriate values in settings.py, particularly the section under DATABASES and MAP_DATABASE:

    cp pycrocosm/settings.py.template pycrocosm/settings.py

    nano pycrocosm/settings.py

To complete the webserver installation, update pycrocosm.settings with details of your database. If you want to access the site from other computers, ALLOWED_HOSTS needs to be set as well. In production, change DEBUG to false and generate a new SECRET_KEY. Create the Django specific tables:

    python manage.py migrate

    python manage.py runserver

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

     python manage.py setmeta readonly 1


Highly loaded servers
---------------------

Highly loaded Linux servers should consider increasing net.core.somaxconn to about 1024. This prevents errors such as "(11: Resource temporarily unavailable) while connecting to upstream". https://blog.narrativ.com/uwsgi-and-nginx-connection-queues-43eaba95047e Running scripts that repeatedly and rapidly access the API can trigger this problem.

Add net.core.somaxconn=1024 to /etc/sysctl.conf for it to become permanent, then reboot. https://serverfault.com/a/271386/375337

All done!

