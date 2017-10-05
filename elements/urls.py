from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'([0-9]+)/ways', views.ways_for_node, name='ways_for_node'),
    url(r'([0-9]+)/full', views.full_obj, name='full_obj'),
    url(r'([0-9]+)', views.element, name='element'),
    url(r'create', views.create, name='create'),
    url(r'relations', views.relations_for_obj, name='relations_for_obj'),
]

