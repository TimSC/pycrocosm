from django.urls import re_path as url

from . import views

app_name = 'extra'

urlpatterns = [
	url(r'^most_active$', views.most_active_users, name='most_active_users'),
]

