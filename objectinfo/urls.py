from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'history', views.history, name='history'),
    url(r'changeset/(?P<changesetId>[0-9]+)', views.changeset, name='changeset'),
]

