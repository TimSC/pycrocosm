# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.index, name='querymap'),
]
