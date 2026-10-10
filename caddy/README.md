Caddy reverse proxy
===================

This folder runs [Caddy](https://caddyserver.com/) in front of pycrocosm. Caddy answers on ports 80 and 443, obtains an HTTPS certificate for the site's name from Let's Encrypt, renews it without being asked, sends the files in the project's `static` folder (the planet dumps among them) straight from disk, and passes every other request to the application.

It is separate from the main `docker-compose.yml`: start the application as usual, then start Caddy from here.

Setting up
----------

	cd caddy
	cp env.template .env
	nano .env

Set `SITE_ADDRESS` to the name the site is reached by. For Caddy to get a certificate, that name must point at this machine and ports 80 and 443 must be reachable from the internet. Then:

	docker compose up -d

The application has to know it is behind the proxy. In the main `.env` (one folder up), set:

	DJANGO_ALLOWED_HOSTS=maps.example.org
	DJANGO_CSRF_TRUSTED_ORIGINS=https://maps.example.org
	DJANGO_BEHIND_HTTPS_PROXY=1

and restart the application. Without the last two, logging in and the admin pages' forms are refused, because the application sees plain HTTP requests whose origin does not match.

`DJANGO_BEHIND_HTTPS_PROXY` makes the application believe the `X-Forwarded-Proto` header. Only set it when every request comes through the proxy: port 8000 should then not be reachable from outside, which you can arrange by publishing it as `127.0.0.1:8000:8000` in the main `docker-compose.yml`, or with a firewall.

Trying it locally
-----------------

Without a `.env`, the site address is `localhost`: Caddy makes a certificate of its own, which browsers and curl warn about. If another web server already has ports 80 and 443, pick others:

	HTTP_PORT=18080 HTTPS_PORT=18443 docker compose up -d
	curl -k https://localhost:18443/api/capabilities

Static files in production
--------------------------

Caddy serves what is in the project's `static` folder. Other static files, such as the admin pages' style sheets, live inside Django and are passed to the application, which only serves them while `DEBUG` is on. With `DEBUG=0` they need collecting into a folder for Caddy to serve; that is not set up yet.

Certificates
------------

They are kept in the `caddy_data` volume. `docker compose down` keeps it; `docker compose down -v` deletes it, and the certificates with it. Let's Encrypt limits how often a name can be issued new ones, so do not delete the volume casually.
