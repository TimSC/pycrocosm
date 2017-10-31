# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.http import HttpResponse
from oauth_provider import forms as oauth_forms

# Create your views here.

@api_view(['GET', 'POST'])
@permission_classes((IsAuthenticated, ))
def oauth_authorize(request, token, callback, params):
	form = oauth_forms.AuthorizeRequestTokenForm(initial={'oauth_token': token.key})

	return render(request, 'oauth/authorize.html', {
		'form': form,
		'token': token })

def oauth_callback(request, **args):
	if 'error' in args:
		return HttpResponse(args["error"])

	return HttpResponse("OAuth token authorized. Please go back to your OAuth client.")

