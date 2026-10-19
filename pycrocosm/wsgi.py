"""
WSGI config for pycrocosm project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pycrocosm.settings")

application = get_wsgi_application()

# Django runs its system checks for manage.py, not for a WSGI server, so
# those that must stop a live site from starting are run here
from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured
from pycrocosm.checks import SECRETS

_errors = [str(error) for error in run_checks(tags=[SECRETS]) if error.is_serious()]
if _errors:
	raise ImproperlyConfigured("\n".join(_errors))
