from django.urls import re_path as url

from . import views

app_name = 'querymap'
urlpatterns = [
	url(r'^map$', views.index, name='querymap'),
]
