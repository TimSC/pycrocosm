# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
try:
    from django.urls import re_path as url
except ImportError:
    from django.conf.urls import url

from . import views

app_name = 'api'
urlpatterns = [
    url(r'^/capabilities$', views.capabilities, name='capabilities'),
    url(r'^/0.6/capabilities$', views.capabilities, name='capabilities2'),
    url(r'^/0.6/permissions$', views.permissions, name='permissions'),
    url(r'/0.6/trackpoints', views.not_implemented),
    url(r'/0.6/gpx', views.not_implemented),
	url(r'/0.6/notes', views.not_implemented),
    url(r'^$', views.apibase, name='apibase'),
    url(r'^/$', views.apibase, name='apibase'),
]
