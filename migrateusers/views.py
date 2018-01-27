# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from .forms import MigrateUserForm
from .models import LegacyAccount
from django.contrib.auth.models import User
from django.urls import reverse
import hashlib
import random
import string

def CheckUsernameExists(username):
	try:
		alreadyRegisteredUser = User.objects.get(username=username)
		return True
	except User.DoesNotExist:
		return False

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
				loginUrl = reverse('accounts:login')

				try:
					alreadyRegisteredUser = User.objects.get(id=existingUser.uid)
					errMsg = "User already migrated, please <a href=\"{}\">log in using normal method</a> with username {}.".format(loginUrl,
alreadyRegisteredUser.username)
				except User.DoesNotExist:

					h = hashlib.new('sha256')
					h.update(form.cleaned_data["password"])
					h.update("\n")
					if existingUser.hashed_password == h.hexdigest():
						#Prevent duplicate usernames
						candidateUsername = existingUser.username
						usernameExists = CheckUsernameExists(candidateUsername)
						while usernameExists:
						
							#Create an alternative username, since someone already has used it
							allchar = string.ascii_letters + string.digits
							candidateUsername = existingUser.username+"".join(random.choice(allchar) for x in range(4))

							usernameExists = CheckUsernameExists(candidateUsername)

						successMsg = "Password ok, welcome back {}. Please log in as {} using <a href=\"{}\">main log in page</a>.".format(existingUser.username, candidateUsername, loginUrl)
						migratedUser = User.objects.create_user(candidateUsername, existingUser.email, form.cleaned_data["password"], id=existingUser.uid)
						
					else:
						errMsg = "Password did not match"
	else:
		form = MigrateUserForm()

	return render(request, 'migrateusers/form.html', {'form': form, 'errMsg': errMsg, 'successMsg': successMsg})

