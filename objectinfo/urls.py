from django.urls import re_path as url

from . import views

app_name = 'objectinfo'
urlpatterns = [
    url(r'history', views.history, name='history'),
    url(r'changeset/(?P<changesetId>[0-9]+)', views.changeset, name='changeset'),
]

