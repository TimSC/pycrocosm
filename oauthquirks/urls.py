from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'request_token', views.request_token, name='oauth_request_token'),
]

