from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^capabilities', views.capabilities, name='capabilities'),
    url(r'^0.6/capabilities', views.capabilities, name='capabilities2'),
]
