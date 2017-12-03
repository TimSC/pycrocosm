# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^/create$', views.create, name='create'),
    url(r'^/(?P<changesetId>[0-9]+)/close$', views.close, name='close'),
    url(r'^/(?P<changesetId>[0-9]+)/download$', views.download, name='download'),
    url(r'^/(?P<changesetId>[0-9]+)/expand_bbox$', views.expand_bbox, name='expand_bbox'),
    url(r'^/(?P<changesetId>[0-9]+)/upload$', views.upload, name='upload'),
    url(r'^/(?P<changesetId>[0-9]+)/comment$', views.comment, name='comment'),
    url(r'^/(?P<changesetId>[0-9]+)/subscribe$', views.subscribe, name='subscribe'),
    url(r'^/(?P<changesetId>[0-9]+)/unsubscribe$', views.unsubscribe, name='unsubscribe'),
    url(r'^/(?P<changesetId>[0-9]+)$', views.changeset, name='changeset'),
    url(r'^s$', views.list, name='list'),
]
