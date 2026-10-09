"""pycrocosm URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
	https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
	1. Add an import:  from my_app import views
	2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
	1. Add an import:  from other_app.views import Home
	2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
	1. Import the include() function: from django.urls import include, re_path as url
	2. Add a URL to urlpatterns:  url(r'^blog/', include('blog.urls'))
"""

from django.urls import include, re_path as url
from django.contrib import admin
from django.contrib.auth import views as auth_views
from objectinfo import views as objectinfo_views
from pycrocosm.ratelimit import rate_limit
from pycrocosm import formats
from users import views as users_views
from overpass import views as overpass_views

login_view = rate_limit("login", "LOGIN_RATE_LIMIT_REQUESTS", "LOGIN_RATE_LIMIT_WINDOW_SECONDS")(
	auth_views.LoginView.as_view())

urlpatterns = [
	# A .json or .xml suffix on an API path selects the response format. A user
	# preference key is free text and may itself end that way, so it is left alone.
	url(r'^(?P<apipath>api/(?!0\.6/user/preferences/).*)\.(?P<fmt>json|xml)$', formats.with_format),
	url(r'^api/interpreter$', overpass_views.interpreter, name='overpass_interpreter'),
	url(r'overpass/', include('overpass.urls', namespace='overpass')),
	url(r'^api/0.6/users$', users_views.users, name='users'),
	url(r'api/0.6/user/', include('users.urls')),
	url(r'api/0.6/changeset', include('changeset.urls', namespace='changeset')),
	url(r'api/0.6/(node|way|relation)/', include('elements.urls')),
	url(r'api/0.6/(nodes|ways|relations)', include('multifetch.urls', namespace='multifetch')),
	url(r'api/0.6/', include('querymap.urls')),
	url(r'api', include('api.urls', namespace='api')),
	url(r'extra/', include('extra.urls', namespace='extra')),
	url(r'admin/', admin.site.urls),
	url(r'^accounts/login/$', login_view, name='login'),
	url(r'^accounts/passwordsent/$', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
	url(r'^accounts/passwordchanged/$', auth_views.PasswordChangeDoneView.as_view(), name='password_change_done'),
	url(r'accounts/', include(('django.contrib.auth.urls', 'accounts'), namespace="accounts")),
	url(r'register/', include('register.urls', namespace="register")),
	url(r'replication/', include('replicate.urls', namespace='replication')),
	url(r'', include('frontpage.urls', namespace='frontpage')),
	url(r'', include('objectinfo.urls', namespace='objectinfo')),
	url(r'oauth2/', include('oauth2.urls', namespace='oauth2')),
]

