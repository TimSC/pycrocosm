nginx configuration
===================

How to run pycrocosm behind nginx, with uwsgi as the application server and systemd to keep it running. The files this refers to, `pycrocosm` (the nginx site) and `pycrocosm.service` (the systemd unit), are in this folder. The main [README](../README.md) covers installing and configuring pycrocosm itself; the `caddy` folder has an alternative to nginx that manages its own HTTPS certificates.

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
