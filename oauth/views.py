# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.http import HttpResponse, HttpResponseBadRequest
from django.db import IntegrityError
from django.utils.crypto import get_random_string
from oauth_provider import forms as oauth_forms
from oauth_provider import models as oauth_models
from oauth_provider import consts as oauth_consts
from .forms import *

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
	resetForm = True
	consumerForm = None
	
	if request.method == "POST":
		resetForm = False
		if "action" not in request.POST:
			return HttpResponseBadRequest("No action specified in POST")
		action = request.POST["action"]
	
		if action == "addconsumer":
			consumerForm = AddProviderForm(request.POST)

			if consumerForm.is_valid():
				try:
					consumer = oauth_models.Consumer(**consumerForm.cleaned_data)
					consumer.user = request.user
					consumer.status = oauth_consts.ACCEPTED #Hopefully not too trusting
					consumer.save()
					consumerForm = None

				except IntegrityError as err:
					ok = False
					consumerKey = consumerForm.cleaned_data["key"]
					if oauth_models.Consumer.objects.filter(key = consumerKey).exists():					
						errStrs.append("Consumer already exists with that key")
					else:
						errStrs.append(str(err))

		if action == "deleteconsumer":
			removeForm = RemoveProviderForm(request.POST)

			if removeForm.is_valid():
				consumerKey = removeForm.cleaned_data["key"]
				consumer = oauth_models.Consumer.objects.get(key=consumerKey)
				consumer.delete()

		if action == "deletetoken":
			removeForm = RemoveTokenForm(request.POST)

			if removeForm.is_valid():
				tokenKey = removeForm.cleaned_data["key"]
				token = oauth_models.Token.objects.get(key=tokenKey)
				token.delete()

	if consumerForm is None:
		consumerForm = AddProviderForm(initial={'key':get_random_string(16),
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

