# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

app_name = 'extra'

urlpatterns = [
	url(r'^most_active$', views.most_active_users, name='most_active_users'),
]

