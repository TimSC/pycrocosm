from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'oauth_authorize', views.oauth_authorize, name='oauth_authorize'),
]

