# -*- coding: utf-8 -*-
try:
    from django.urls import re_path as url
except ImportError:
    from django.conf.urls import url

from . import views

app_name = 'oauth2'
urlpatterns = [
    url(r'authorize', views.authorize, name='authorize'),
    url(r'token', views.token, name='token'),
]

