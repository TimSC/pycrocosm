# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.auth import login, authenticate
from django.shortcuts import render, redirect
from django.http import HttpResponse
from . import forms
from querymap.views import p

# Create your views here.

# Based on https://simpleisbetterthancomplex.com/tutorial/2017/02/18/how-to-create-user-sign-up-view.html

def index(request):
	if request.method == 'POST':
		form = forms.RegisterForm(request.POST)
		if form.is_valid():
			t = p.GetTransaction(b"EXCLUSIVE")
			cid = t.GetAllocatedId(b"uid")
			userObj = form.save(commit=False)
			userObj.id = cid
			userObj.save()
			t.Commit()
			username = form.cleaned_data.get('username')
			raw_password = form.cleaned_data.get('password1')
			user = authenticate(username=username, password=raw_password)
			login(request, user)
			return redirect('/')
	else:
		form = forms.RegisterForm()
	return render(request, 'register/index.html', {'form': form})

