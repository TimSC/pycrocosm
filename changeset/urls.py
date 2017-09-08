from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^/create$', views.create, name='create'),
    url(r'^/([0-9]+)/close$', views.close, name='close'),
    url(r'^/([0-9]+)/download$', views.download, name='download'),
    url(r'^/([0-9]+)/expand_bbox$', views.expand_bbox, name='expand_bbox'),
    url(r'^/([0-9]+)/upload$', views.upload, name='upload'),
    url(r'^/([0-9]+)/comment$', views.comment, name='comment'),
    url(r'^/([0-9]+)/subscribe$', views.subscribe, name='subscribe'),
    url(r'^/([0-9]+)/unsubscribe$', views.unsubscribe, name='unsubscribe'),
    url(r'^/([0-9]+)$', views.changeset, name='changeset'),
    url(r'^s$', views.list, name='list'),
]
