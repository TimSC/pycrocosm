# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.http import HttpResponse

# Create your views here.

@api_view(['GET'])
@permission_classes((IsAuthenticated, ))
def oauth_authorize(request, token, callback, params):
	return HttpResponse('Confirm oauth access? for %s with params: %s.' % (token.consumer.name, params))

