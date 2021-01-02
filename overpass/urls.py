# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

app_name = 'overpass'
urlpatterns = [
    url(r'xapi/(.*)', views.xapi1, name='xapi1'),
    url(r'xapi_meta', views.xapi2, name='xapi2'),
]

