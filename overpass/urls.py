from django.urls import re_path as url

from . import views

app_name = 'overpass'
urlpatterns = [
    url(r'^api/interpreter$', views.interpreter, name='interpreter'),
    url(r'xapi/(.*)', views.xapi1, name='xapi1'),
    url(r'xapi_meta', views.xapi2, name='xapi2'),
]

