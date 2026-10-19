from django.urls import path

from . import views

app_name = 'oauth2'
# Exact paths: a regular expression such as r'authorize' would also match any
# path containing it, authorized_applications among them
urlpatterns = [
	path('authorize', views.authorize, name='authorize'),
	path('token', views.token, name='token'),
	path('applications', views.applications, name='applications'),
	path('application/<str:client_id>', views.application_detail, name='applications_detail'),
	path('authorized_applications', views.authorized_applications, name='authorized_applications'),
]
