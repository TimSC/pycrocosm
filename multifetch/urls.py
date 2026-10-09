from django.urls import re_path as url

from . import views

app_name = 'multifetch'
urlpatterns = [
    url(r'', views.collection, name='multifetch'),
]

