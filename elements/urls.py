# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
try:
    from django.urls import re_path as url
except ImportError:
    from django.conf.urls import url

from . import views

app_name = 'elements'
urlpatterns = [
    url(r'([0-9]+)/ways', views.ways_for_node, name='ways_for_node'),
    url(r'([0-9]+)/full', views.full_obj, name='full_obj'),
    url(r'([0-9]+)/relations', views.relations_for_obj, name='relations_for_obj'),
    url(r'([0-9]+)/history', views.object_history, name='object_history'),
    url(r'([0-9]+)/bbox', views.object_bbox, name='object_bbox'),
    url(r'([0-9]+)/([0-9]+)', views.object_version, name='object_version'),
    url(r'([0-9]+)', views.element, name='element'),
    url(r'create', views.create, name='create'),
]

