# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from .forms import MigrateUserForm
from .models import LegacyAccount
import hashlib

def index(request):

	errMsg = None
	successMsg = None
	if request.method == 'POST':
		form = MigrateUserForm(request.POST)
		if form.is_valid():
			existingUser = None
			try:
				existingUser = LegacyAccount.objects.get(username=form.cleaned_data["username_or_email"])
			except LegacyAccount.DoesNotExist:
				try:
					existingUser = LegacyAccount.objects.get(email=form.cleaned_data["username_or_email"])
				except LegacyAccount.DoesNotExist:
					pass
			if existingUser is None:
				errMsg = "User not found"
			else:
				h = hashlib.new('sha256')
				h.update(form.cleaned_data["password"])
				h.update("\n")
				if existingUser.hashed_password == h.hexdigest():
					successMsg = "Password ok, welcome back {}".format(existingUser.username)
				else:
					errMsg = "Password did not match"
	else:
		form = MigrateUserForm()

	return render(request, 'migrateusers/form.html', {'form': form, 'errMsg': errMsg, 'successMsg': successMsg})

