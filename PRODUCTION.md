Running pycrocosm in production
===============================

The main [README](README.md) sets pycrocosm up for development: Django's own server, `DEBUG` on, and PostgreSQL as installed. This page lists what to change for a public server, and how to tune PostgreSQL so map queries are fast. It assumes the Docker setup from the README, with PostgreSQL on the host.

Checklist
---------

1. Set the production values in `.env`: `DEBUG=0`, a new `SECRET_KEY`, the site's name in `DJANGO_ALLOWED_HOSTS` (see [Application settings](#application-settings)).
2. Make the changes to `pycrocosm/settings.py` under [Settings file changes](#settings-file-changes): static files, a shared cache, secure cookies.
3. Run the application with gunicorn instead of `runserver`, reachable only from this machine ([Application server](#application-server)).
4. Put Caddy in front of it for HTTPS ([Reverse proxy](#reverse-proxy)).
5. Tune PostgreSQL, build the indices, and decide on `useBboxInQuery` ([PostgreSQL for fast queries](#postgresql-for-fast-queries)).
6. Schedule the regular jobs and backups ([Regular jobs and backups](#regular-jobs-and-backups)).
7. Read [Known issues](#known-issues): rate limits need a code change to work behind a proxy.
8. Let editors sign in: register JOSM as an OAuth2 application ([JOSM and other editors](#josm-and-other-editors)).

Application settings
--------------------

These go in `.env`, beside `docker-compose.yml` (`env.template` is a starting point).

| Variable | Production value | Why |
|---|---|---|
| `DEBUG` | `0` | It defaults to on, which shows error details, including settings, to anyone. |
| `SECRET_KEY` | A long random string, kept private | Signs sessions and, unless `OAUTH2_JWT_SECRET` is set, OAuth2 tokens. Without it a key published in the source is used, and with `DEBUG` off the site refuses to start (system check `pycrocosm.E001`, which gunicorn runs too). Make one with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. |
| `OAUTH2_JWT_SECRET` | Optional; another random string | Lets the token key be changed without logging everyone out of the website. |
| `DJANGO_ALLOWED_HOSTS` | `maps.example.org` | The names the site answers to; others are refused. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://maps.example.org` | Needed for logging in and the admin forms behind an HTTPS proxy. |
| `DJANGO_BEHIND_HTTPS_PROXY` | `1` | Believe the proxy's `X-Forwarded-Proto`. Set only when every request comes through the proxy. |
| `DJANGO_PUBLIC_URL` | `https://maps.example.org/` | The address extract files and planet dumps give as the server to keep them up to date from (`update_url`). pgmap's own tools read `PGMAP_PUBLIC_URL`; set both, or set `public_url` in `pgmap/config.cfg`, which the web server reads too. |
| `DJANGO_DB_*`, `DJANGO_MAP_DB_*`, `PGMAP_*` | The database connections | `DJANGO_DB_*` is the settings database; the map database comes from `pgmap/config.cfg`, overridden by `PGMAP_*` (pgmap's tools read these too) and, for the web server only, `DJANGO_MAP_DB_*`. Use a password that is not `password`. |
| `OAUTH2_ACCESS_TOKEN_SECONDS` | `0` (the default) | How long an OAuth2 token lasts. `0` is until it is revoked, as on openstreetmap.org: editors such as JOSM keep their token and cannot refresh it, so a limited lifetime makes them ask the user to authorise again each time it runs out. |
| `LOGIN_RATE_LIMIT_*`, `OAUTH2_RATE_LIMIT_*`, `OVERPASS_RATE_LIMIT_*` | Defaults are reasonable | Requests per client address in each window; zero turns a limit off. See [Known issues](#known-issues). |

Settings that are not read from the environment are set in `pycrocosm/settings.py` (copied from `settings.py.template`). Worth reviewing:

* `OVERPASS_TIMEOUT_MAXIMUM` (180 seconds), `OVERPASS_ELEMENTS_MAXIMUM` and `OVERPASS_AREA_MAXIMUM` bound what one Overpass query can cost. `AREA_MAXIMUM` (0.25 square degrees) does the same for the map call.
* `TIMEOUT_SECONDS` (300) is the timeout the API advertises to editors. Keep the application server's and the proxy's timeouts at least this long.
* `XML_UPLOAD_MAXIMUM_BYTES` (10 MB) is the largest changeset upload. The proxy must allow bodies this large.
* `STATUS_DATABASE`, `STATUS_API` and `STATUS_GPX` are what the API's capabilities tell editors (`"online"`, `"readonly"` or `"offline"`); they do not stop edits by themselves. Read-only mode is set in the map database instead, with `manage.py setmeta readonly 1` (and `0` to end it; see the README's "Read only mode"). Use it while long admin tool steps run, such as "Update way/relation bboxes".
* `COPYRIGHT`, `ATTRIBUTION`, `LICENSE` and `GENERATOR` are what the API says about the data.

Settings file changes
---------------------

Some production needs are not covered by the template yet. Add these to `pycrocosm/settings.py`.

### Static files

With `DEBUG` off, Django no longer serves the files in its apps, such as the admin pages' style sheets. Collect them into one folder that the proxy serves:

```python
STATIC_ROOT = os.path.join(BASE_DIR, 'static_collected')
```

then, after each upgrade:

	docker compose run --rm web python3 manage.py collectstatic --noinput --ignore planet

`--ignore planet` matters: `collectstatic` copies the project's own `static` folder too, and that holds the planet dumps, which would otherwise be copied in full every time. Serve `/static/` from the collected folder as well as from `static/` (see [Reverse proxy](#reverse-proxy)).

### A cache shared by all workers

The rate limits count requests in Django's cache. The default cache lives in each worker process's memory, so with several workers each counts on its own, and a client gets several times the limit. The database cache needs nothing more installed:

```python
CACHES = {
	'default': {
		'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
		'LOCATION': 'pycrocosm_cache',
	}
}
```

then create its table once:

	docker compose run --rm web python3 manage.py createcachetable

Memcached or Redis are quicker under heavy load, but need another service and a Python package.

### Secure cookies

Behind HTTPS, keep the session and CSRF cookies off plain HTTP:

```python
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

Application server
------------------

`python3 manage.py runserver` is for development only. Use gunicorn, which `requirements.txt` already installs. In `docker-compose.yml`:

```yaml
services:
  web:
    build: ./
    command: >
      gunicorn pycrocosm.wsgi:application
      --bind 0.0.0.0:8000
      --worker-class gthread --workers 4 --threads 4
      --timeout 330 --graceful-timeout 60
      --max-requests 2000 --max-requests-jitter 200
    ports:
      - "127.0.0.1:8000:8000"
    restart: unless-stopped
    # volumes, env_file and extra_hosts as before
```

* **`127.0.0.1:8000:8000`** publishes the port to this machine only, so nothing reaches the application except through the proxy. That is what makes `DJANGO_BEHIND_HTTPS_PROXY` safe.
* **Threaded workers** (`gthread`). Map calls, extract downloads and planet dump links can hold a request open for minutes; with plain workers each one would block a whole process. Start with about two workers per processor core and four threads each, and adjust to the load.
* **`--timeout`** must exceed the longest request: `TIMEOUT_SECONDS` (300) and the Overpass maximum (180). gunicorn's default of 30 seconds would cut off large map calls and downloads.
* **`--max-requests`** restarts each worker now and then, which bounds slow memory growth. Each thread reconnects to the map database by itself.
* **Connections.** Every thread keeps one connection to the map database and one to the settings database. 4 workers × 4 threads uses 32 connections; size PostgreSQL's `max_connections` for that (see below).

Long-running jobs should not go through the web server: use the manage.py commands (`updateextracts`, `dumpplanet`, `saveextract` and so on) from cron, as below.

Reverse proxy
-------------

**Recommended: Caddy**, in the `caddy` folder. It answers on ports 80 and 443, gets and renews an HTTPS certificate from Let's Encrypt by itself, sends files in `static/`, the planet dumps among them, straight from disk, and passes everything else to the application. It has no limit on upload size and no timeout on slow answers by default, both of which suit the API. Setting up is in [caddy/README.md](caddy/README.md); in short:

	cd caddy
	cp env.template .env      # set SITE_ADDRESS=maps.example.org
	docker compose up -d

and set `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` and `DJANGO_BEHIND_HTTPS_PROXY` as above.

For the collected static files, mount `static_collected` into the Caddy container as well, in `caddy/docker-compose.yml`:

```yaml
    volumes:
      - ../static_collected:/srv/collected/static:ro
```

and in the Caddyfile, beside the existing `@on_disk` block, serve what is found there:

```
	@collected {
		path /static/*
		file {
			root /srv/collected
		}
	}
	handle @collected {
		root * /srv/collected
		file_server
	}
```

Mounting the folder as `/srv/collected/static` means a request for `/static/admin/css/base.css` finds `/srv/collected/static/admin/css/base.css`. Anything in neither folder still goes to the application.

**nginx** works as well, but the files in the `nginx` folder are for an older uwsgi setup. With gunicorn, proxy over HTTP and change nginx's defaults, which are too small for the API:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 12m;      # changeset uploads up to XML_UPLOAD_MAXIMUM_BYTES; the default is 1m
    proxy_read_timeout 330s;       # long map calls and downloads; the default is 60s
    proxy_buffering off;           # stream large downloads instead of holding them on disk first
}
location /static/ {
    alias /srv/pycrocosm/static/;
}
```

You then also need certbot or similar for certificates.

PostgreSQL for fast queries
---------------------------

These settings are for serving queries. Settings for loading a planet quickly are in [pgmap/README.md](pgmap/README.md), "Recommended PostgreSQL settings for a large import". The figures below suppose a machine with 64 GB of memory and SSD storage that runs the database and the application; scale them to yours.

`SHOW config_file;` in psql says where `postgresql.conf` is. Settings marked *restart* need PostgreSQL restarted; the rest take effect on `sudo systemctl reload postgresql`.

### Memory

```
shared_buffers = 16GB              # restart; about a quarter of memory
effective_cache_size = 44GB        # about two thirds of memory: shared_buffers plus the OS file cache
work_mem = 64MB                    # memory for each sort or hash in a query
temp_buffers = 64MB                # memory for each connection's temporary tables
maintenance_work_mem = 2GB         # index builds and VACUUM
```

* **`shared_buffers`** keeps the most used parts of the tables and indices in memory. The map query reads nodes through their spatial index, and the more of that index stays in memory, the faster it runs.
* **`effective_cache_size`** sets no memory aside. It tells the planner how much data is likely to be cached, which makes it more willing to use indices.
* **`work_mem`** applies to each sort or hash, and one query can have several, in each connection. The map query sorts and joins the objects it selects, so the default of 4MB makes larger areas spill to disk. 64MB × 32 connections × a couple per query stays well within 64 GB; lower it if you run many more connections.
* **`temp_buffers`**: each map query collects what it selects in temporary tables before writing it out. The default of 8MB is small for a busy area; memory is only used by connections that need it.

### Storage and planner

```
random_page_cost = 1.1             # on SSD; the default 4 assumes spinning disks
effective_io_concurrency = 200     # on SSD
```

With the defaults the planner believes that reading by index is far dearer than it is on SSD, and will scan a whole table when an index would be quicker. On spinning disks leave both alone.

### Connections

```
max_connections = 100              # restart
```

Allow for every application thread (two connections each, as above), the cron jobs, pgmap's admin tool and psql sessions, with room to spare. If you need many more than a couple of hundred, put PgBouncer in front of PostgreSQL rather than raising this a long way: every connection costs memory and the `work_mem`/`temp_buffers` allowances are per connection.

### Protection against stuck sessions

```
idle_in_transaction_session_timeout = 10min
```

This ends a connection that leaves a transaction open and idle, such as a crashed client, so it cannot hold locks or stop vacuuming. It does not affect a statement that is running, so long admin tool steps are safe.

Do not set a global `statement_timeout`: the admin tool's index builds and imports take hours. If you want one for the web server alone, give the web server its own database role and set it for that role (`ALTER ROLE pycrocosm_web SET statement_timeout = '330s';`).

### JIT

On large tables PostgreSQL may decide to compile parts of a query with JIT, which takes a noticeable fraction of a second and seldom pays off for the short queries an API makes. If `EXPLAIN ANALYZE` of slow requests shows time under "JIT", turn it off:

```
jit = off
```

### Indices, statistics and `useBboxInQuery`

The settings matter less than having the right indices:

1. **After an import**, run "Create indicies" in pgmap's admin tool, then `ANALYZE` the map database (`vacuumdb --analyze-only db_map`), so the planner knows how large the tables are. Autovacuum keeps the statistics current afterwards; leave it on.
2. **Bounding boxes and search indices.** "Update way/relation bboxes" and "Create/drop bbox indices" in the admin tool, then `useBboxInQuery` set to 1 in the admin panel, make the map call, Overpass and XAPI find ways and relations in an area with one indexed search, and tag searches use an index. This is the largest single speed-up for busy servers. The README's "Bounding boxes and search indices" explains the steps and what changes; on a planet the box step can take days, so plan for it (read-only mode while it runs).
3. **Index versions.** After upgrading pycrocosm, the admin tool's index version option says whether the indices are those the current schema expects.

Stored extracts saved while `useBboxInQuery` is on are kept up to date by being taken from the map afresh, which costs time in proportion to their size. Large extracts kept up to date often are cheaper with it off; see "Updating database extracts" in the README.

### Durability

Leave `fsync`, `full_page_writes` and `synchronous_commit` on: edits are people's work, and turning these off risks losing or corrupting data in a crash. If you changed `wal_level` to `minimal` for an import, set it back to `replica` (with `max_wal_senders` at its default) before relying on replication or log archiving for backups.

Edit notifications
------------------

If you run the edit notification server ([notify/README.md](notify/README.md)), open its port (7500 by default) only to the clients that need it: the connection is not encrypted and clients are not identified. It needs a direct connection to PostgreSQL, not one through PgBouncer in transaction mode, and counts against `max_connections` like one more application thread. Its per-connection limits (`--max-per-address`, `--requests-per-second` and others) have defaults that suit most servers.

JOSM and other editors
----------------------

Editors sign in with OAuth2, as with openstreetmap.org. Each needs an application registered on this server; JOSM additionally uses the server's default application, so that its fully automatic authorisation works with nothing set in JOSM.

**Registering JOSM.** With `manage.py migrate` run (the OAuth2 tables need it), sign in to the site as the account that will own the application, and open `/oauth2/applications`:

* **Name**: JOSM. Users see it on the page asking them to allow access.
* **Redirect URIs**: `http://127.0.0.1:8111/oauth_authorization`. This is JOSM's own callback: JOSM listens on port 8111 of the user's computer for the browser to come back to it with the code. It is the same for every JOSM user, and plain HTTP to `127.0.0.1` is right here: it never leaves their machine. Add `urn:ietf:wg:oauth:2.0:oob` on another line if the code is also to be copied by hand.
* **Permissions**: those JOSM asks for: read and change user preferences (`read_prefs`, `write_prefs`), change the map (`write_api`), read and upload GPS traces (`read_gpx`, `write_gpx`) and change notes (`write_notes`). An application may be asked only for what it is registered with, so leaving one out makes JOSM's request fail with `invalid_scope`.

Keep a note of the client ID; JOSM does not use a client secret (it uses PKCE instead).

**Making it the default.** JOSM sends no client ID to a server it does not know. In the admin panel (`/admin/`, OAuth2 applications), open the JOSM application and tick **Is default**; requests without a client ID then use it. Only one application can be the default. Without one, each user enters the client ID in JOSM instead (Preferences, OSM server, untick the default OAuth parameters).

**Users** then set JOSM's OSM server URL to `https://maps.example.org/api` and choose fully automatic authorisation: their browser shows this server's page asking them to allow JOSM, and JOSM receives a token, which lasts until they revoke it at `/oauth2/authorized_applications` (see `OAUTH2_ACCESS_TOKEN_SECONDS`). JOSM finds the endpoints from `/.well-known/oauth-authorization-server` on the standard HTTPS port, and otherwise at `/api/authorize` and `/api/token`, next to the API, which this server also answers.

Tokens are accepted only over HTTPS (or with `DEBUG` on), so this needs the [reverse proxy](#reverse-proxy) with `DJANGO_BEHIND_HTTPS_PROXY=1`, for Django to know requests came by HTTPS.

Other editors are registered the same way, with the redirect URI and permissions they document; those that keep a client secret are given the one shown when the application is registered.

Regular jobs and backups
------------------------

The README's "Scheduled tasks" lists the jobs to run from cron: `closeoldchangesets` hourly, `updateextracts` as often as extracts need to be fresh, and `dumpplanet` monthly. None runs twice at once, so overlapping schedules do no harm.

Back up both databases:

* **The settings database** (user accounts and preferences, OAuth2 applications and their grants, sessions) is small: `pg_dump -Fc db_settings` daily.
* **The map database** (the map, its history, changesets, edit activity and stored extracts) is large. `pg_dump -Fd -j 8 db_map` takes a consistent copy in parallel, but for a planet it is slow to restore. A base backup with archived write-ahead log (`pg_basebackup`, or a tool such as pgBackRest) restores faster and to any moment, and needs `wal_level = replica`.

Test a restore now and then. Keep `pgmap/config.cfg` and `.env` with the backups, but somewhere private: they hold the database passwords and the secret key.

Security
--------

* Keep port 8000 (the application) and 5432 (PostgreSQL) closed to the outside: publish the application on `127.0.0.1` as above, and let PostgreSQL accept only local connections and those from Docker's network (`listen_addresses` and `pg_hba.conf`).
* Run the application as an ordinary user, not root. The Docker setup does; the systemd unit in the `nginx` folder runs as root and should be changed if used.
* Make the first admin account with `manage.py createsuperuser`, and give staff access sparingly: the admin panel can delete extracts and change map parameters.

Known issues
------------

* **Rate limits behind a proxy.** The rate limits identify clients by `REMOTE_ADDR`. Behind a reverse proxy that is the proxy's own address, so every client shares one allowance, and a busy site will turn ordinary users away. Until the limits read the client's address from the proxy's `X-Forwarded-For`, which needs a code change that trusts that header only from the proxy, either raise the limits well above one client's needs or set them to zero:

	  OVERPASS_RATE_LIMIT_REQUESTS=0
	  LOGIN_RATE_LIMIT_REQUESTS=100

  and rely on the proxy to limit request rates instead.
* **Static files** are not collected by any setup in the repository yet; follow [Static files](#static-files) above.
