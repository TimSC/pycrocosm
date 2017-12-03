# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserData, UserPreference

# Register your models here.

# Define an inline admin descriptor for UserData model
# which acts a bit like a singleton
class UserDataInline(admin.StackedInline):
    model = UserData
    can_delete = False
    verbose_name_plural = 'User Data'

# Define a new User admin
class UserAdmin(BaseUserAdmin):
    inlines = (UserDataInline, )

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)

admin.site.register(UserPreference)

