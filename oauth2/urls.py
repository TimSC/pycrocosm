from django.urls import path
from django.urls import re_path as url

from . import views

app_name = 'oauth2'
urlpatterns = [
    url(r'authorize', views.authorize, name='authorize'),
    url(r'token', views.token, name='token'),
    url(r'applications', views.applications, name='applications'),
	path('application/<str:client_id>', views.application_detail, name='applications_detail'),
]

