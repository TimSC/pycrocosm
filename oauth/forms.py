from django import forms
from django.conf import settings

class AddProviderForm(forms.Form):
	name = forms.CharField(label='Name', max_length=255, min_length=3)
	description = forms.CharField(label='Description', max_length=1024*10)
	key = forms.CharField(label='Key', max_length=settings.OAUTH_PROVIDER_CONSUMER_KEY_SIZE, min_length=16)
	secret = forms.CharField(label='Secret', max_length=settings.OAUTH_PROVIDER_SECRET_SIZE, min_length=16)

