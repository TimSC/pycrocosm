from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'details', views.details, name='details'),
	url(r'preferences/(.*)', views.preferences_put, name='preferences_put'),
    url(r'preferences', views.preferences, name='preferences'),
]
