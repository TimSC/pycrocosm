# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

app_name = 'users'
urlpatterns = [
	url(r'details', views.details, name='details'),
	url(r'preferences/(.*)', views.preferences_put, name='preferences_put'),
	url(r'preferences', views.preferences, name='preferences'),
	url(r'gpx_files', views.not_implemented),
]
