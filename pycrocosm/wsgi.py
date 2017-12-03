# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

"""
WSGI config for pycrocosm project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.11/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pycrocosm.settings")

application = get_wsgi_application()
