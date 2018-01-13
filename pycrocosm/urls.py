# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

"""pycrocosm URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
	https://docs.djangoproject.com/en/1.11/topics/http/urls/
Examples:
Function views
	1. Add an import:  from my_app import views
	2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
	1. Add an import:  from other_app.views import Home
	2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
	1. Import the include() function: from django.conf.urls import url, include
	2. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))
"""

from django.conf.urls import include, url
from django.contrib import admin
from django.contrib.auth import views as auth_views
from oauth_provider import views as oauth_views
from objectinfo import views as objectinfo_views

urlpatterns = [
	url(r'api/0.6/map', include('querymap.urls')),
	url(r'api/0.6/user/', include('users.urls')),
	url(r'api/0.6/changeset', include('changeset.urls', namespace='changeset')),
	url(r'api/0.6/(node|way|relation)/', include('elements.urls')),
	url(r'api/0.6/(nodes|ways|relations)', include('multifetch.urls', namespace='multifetch')),
	url(r'api', include('api.urls', namespace='api')),
	url(r'extra/', include('extra.urls', namespace='extra')),
	url(r'admin/', admin.site.urls),
	url(r'^accounts/passwordsent/$', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
	url(r'^accounts/passwordchanged/$', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),
	url(r'accounts/', include(('django.contrib.auth.urls', 'accounts'), namespace="accounts")),
	url(r'register/', include('register.urls', namespace="register")),
	url(r'replication/', include('replicate.urls', namespace='replication')),
	url(r'^oauth/request_token$', oauth_views.request_token,      name='oauth_request_token'),
	url(r'^oauth/authorize$',     oauth_views.user_authorization, name='oauth_user_authorization'),
	url(r'^oauth/access_token$',  oauth_views.access_token,	      name='oauth_access_token'),
	url(r'oauth/',  include('oauth.urls', namespace="oauth")),
	url(r'', include('frontpage.urls', namespace='frontpage')),
	url(r'migrate/', include('migrateusers.urls', namespace='migrateusers')),
	url(r'', include('objectinfo.urls', namespace='objectinfo')),
]


