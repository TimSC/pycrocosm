# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
try:
    from django.urls import re_path as url
except ImportError:
    from django.conf.urls import url

from . import views

app_name = 'querymap'
urlpatterns = [
	url(r'^map$', views.index, name='querymap'),
	url(r'^historic_map$', views.historic_map, name='historic_map'),
]
