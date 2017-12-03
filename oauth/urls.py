# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'oauth_authorize', views.oauth_authorize, name='oauth_authorize'),
    url(r'oauth_callback', views.oauth_callback, name='oauth_callback'),
	url(r'manage', views.manage, name='oauth_manage'),
]

