# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.http import HttpResponse
from django.db import IntegrityError
from django.utils.crypto import get_random_string
from oauth_provider import forms as oauth_forms
from oauth_provider import models as oauth_models
from . import forms

# Create your views here.

@api_view(['GET', 'POST'])
@permission_classes((IsAuthenticated, ))
def oauth_authorize(request, token, callback, params):
	form = oauth_forms.AuthorizeRequestTokenForm(initial={'oauth_token': token.key})

	return render(request, 'oauth/authorize.html', {
		'form': form,
		'token': token })

@api_view(['GET', 'POST'])
@permission_classes((IsAuthenticated, ))
def oauth_callback(request, **args):
	if 'error' in args:
		return HttpResponse(args["error"])

	return HttpResponse("OAuth token authorized. Please go back to your OAuth client.")

@api_view(['GET', 'POST'])
@permission_classes((IsAuthenticated, ))
def manage(request):

	ok = True
	errStrs = []
	
	if request.method == "POST":
		consumerForm = forms.AddProviderForm(request.POST)

		if consumerForm.is_valid():
			try:
				consumer = oauth_models.Consumer(**consumerForm.cleaned_data)
				consumer.user = request.user
				consumer.save()

			except IntegrityError as err:
				ok = False
				consumerKey = consumerForm.cleaned_data["key"]
				if oauth_models.Consumer.objects.filter(key = consumerKey).exists():					
					errStrs.append("Consumer already exists with that key")
				else:
					errStrs.append(str(err))

	else:
		consumerForm = forms.AddProviderForm(initial={'key':get_random_string(16),
			'secret':get_random_string(64)})

	publicConsumers = oauth_models.Consumer.objects.filter(user = None)
	userConsumers = oauth_models.Consumer.objects.filter(user = request.user)
	matchedConsumers = list(publicConsumers) + list(userConsumers)

	tokens = oauth_models.Token.objects.filter(user = request.user)

	return render(request, 'oauth/manage.html', {
		'user': request.user,
		'tokens': tokens,
		'ok': ok,
		'err_strs': errStrs,
		'consumer_form': consumerForm,
		'consumers': matchedConsumers})

