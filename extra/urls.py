from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^getaffected$', views.getaffected, name='getaffected'),
]
