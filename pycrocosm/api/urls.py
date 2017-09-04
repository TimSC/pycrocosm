from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^capabilities', views.capabilities, name='capabilities'),
    url(r'^0.6/capabilities', views.capabilities, name='capabilities2'),
    url(r'^0.6/', views.api06, name='api06'),
    url(r'^$', views.index, name='index'),
]
