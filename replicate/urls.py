# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

app_name = 'replicate'
urlpatterns = [
    url(r'^now$', views.timenow, name='timenow'),
	url(r'^(minute|hour|day)/$', views.catalog, name='catalog'),
	url(r'^(minute|hour|day)/([0-9]+)/$', views.catalog2, name='catalog2'),
	url(r'^(minute|hour|day)/([0-9]+)/([0-9]+)/$', views.catalog3, name='catalog3'),
	url(r'^(minute|hour|day)/([0-9]+)/([0-9]+)/([0-9]+).state.txt$', views.state, name='diffstate'),
	url(r'^(minute|hour|day)/([0-9]+)/([0-9]+)/([0-9]+).osc.gz$', views.diffgz, name='diffdatagz'),
	url(r'^(minute|hour|day)/([0-9]+)/([0-9]+)/([0-9]+).osc$', views.diff, name='diffdata'),
	url(r'edit_activity/([0-9]+)', views.get_edit_activity, name='get_edit_activity'),	
	url(r'edit_activities', views.query_edit_activity_by_timestamp, name='query_edit_activity_by_timestamp'),	
    url(r'^diff$', views.customdiff, name='customdiff'),
    url(r'^$', views.index, name='replicate'),
]
