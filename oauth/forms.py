from django import forms
from django.conf import settings

class AddProviderForm(forms.Form):
	name = forms.CharField(label='Name', max_length=255, min_length=3)
	description = forms.CharField(label='Description', max_length=1024*10, required = False)

class RemoveProviderForm(forms.Form):
	key = forms.CharField(label='Key', max_length=1024)

class RemoveTokenForm(forms.Form):
	key = forms.CharField(label='Key', max_length=1024)

