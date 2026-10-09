from django.urls import re_path as url

from . import views

app_name = 'querymap'
urlpatterns = [
	url(r'^map$', views.index, name='querymap'),
	url(r'^historic_map$', views.historic_map, name='historic_map'),
]
