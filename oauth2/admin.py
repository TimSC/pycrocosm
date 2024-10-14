from django.contrib import admin
from .models import Oauth2Application, Oauth2Authorization

# Register your models here.
class Oauth2ApplicationAdmin(admin.ModelAdmin):
	pass

class Oauth2AuthorizationAdmin(admin.ModelAdmin):
	pass

admin.site.register(Oauth2Application, Oauth2ApplicationAdmin)
admin.site.register(Oauth2Authorization, Oauth2AuthorizationAdmin)

