from django import forms

class MigrateUserForm(forms.Form):
	username_or_email = forms.CharField(label='Username or email', max_length=255)
	password = forms.CharField(label='Password', max_length=255, widget=forms.PasswordInput)

