from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^getaffected/(node|way|relation)/([0-9]+)$', views.get_affected, name='getaffected'),
    url(r'^getaffected$', views.get_affected_from_upload, name='get_affected_from_upload'),
]

