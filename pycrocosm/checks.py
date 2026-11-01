"""System checks of pycrocosm's own settings.

Django runs system checks before runserver, migrate and most management
commands, but not when a WSGI server such as gunicorn loads the site, so
pycrocosm/wsgi.py runs the ones tagged SECRETS itself and refuses to start.
"""

from django.conf import settings
from django.core import checks

SECRETS = "pycrocosm_secrets"

# Secret keys published with the source, in settings.py.template, which
# anyone can read: fine for development, never for a live site
PUBLISHED_SECRET_KEYS = {
	'juc)pa31j2p$3t^7u4(f#&1%lf+@09sk8wx%%tg&iazlefvqqh',
}

@checks.register(SECRETS)
def check_secret_keys(app_configs=None, **kwargs):
	"""With DEBUG off, SECRET_KEY must not be a key published with the source:
	anyone could forge session cookies and password reset links with it."""
	if settings.DEBUG or settings.SECRET_KEY not in PUBLISHED_SECRET_KEYS:
		return []
	return [checks.Error(
		"SECRET_KEY is the key published with pycrocosm's source, and DEBUG is off",
		hint="Set the SECRET_KEY environment variable to a long random string of your own, such as "
			"python3 -c \"import secrets; print(secrets.token_urlsafe(50))\" gives. See PRODUCTION.md.",
		id="pycrocosm.E001")]
