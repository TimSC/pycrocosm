# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

app_name = 'objectinfo'
urlpatterns = [
    url(r'history', views.history, name='history'),
    url(r'changeset/(?P<changesetId>[0-9]+)', views.changeset, name='changeset'),
]

