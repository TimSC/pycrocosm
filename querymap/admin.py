from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from . import mapmeta
from .models import MapParameter

@admin.register(MapParameter)
class MapParameterAdmin(admin.ModelAdmin):
	"""Shows and changes the metadata parameters pgmap keeps in the map database."""

	# The stock add, change and delete views use the ORM, which has no table
	# to read, so they stay disabled. Changes have their own views below.
	def has_add_permission(self, request):
		return False

	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def has_view_permission(self, request, obj=None):
		return request.user.is_active and request.user.is_staff

	def has_module_permission(self, request):
		return self.has_view_permission(request)

	def may_edit(self, request):
		# These change how the whole map behaves, so not every staff user may
		user = request.user
		return user.is_active and user.is_staff and (
			user.is_superuser or user.has_perm("querymap.change_mapparameter"))

	def get_urls(self):
		view = self.admin_site.admin_view
		# Listed first so they take precedence over the stock object routes.
		return [
			path("set/", view(self.set_view), name="querymap_mapparameter_set"),
			path("remove/", view(self.remove_view), name="querymap_mapparameter_remove"),
		] + super().get_urls()

	def changelist_view(self, request, extra_context=None):
		context = dict(self.admin_site.each_context(request))
		context.update({
			"title": "Map parameters",
			"opts": self.model._meta,
			"parameters": [],
			"static_parameters": [],
			"error": None,
			"may_edit": self.may_edit(request),
		})
		try:
			context["parameters"], context["static_parameters"] = mapmeta.list_parameters()
		except Exception as err:
			context["error"] = str(err)
		context.update(extra_context or {})
		return TemplateResponse(request, "admin/querymap/mapparameter/change_list.html", context)

	def changelist_redirect(self):
		return HttpResponseRedirect(reverse("admin:querymap_mapparameter_changelist"))

	@method_decorator(csrf_protect)
	@method_decorator(require_POST)
	def set_view(self, request):
		"""Set one parameter, new or existing."""
		if not self.may_edit(request):
			raise PermissionDenied
		key = request.POST.get("key", "").strip()
		value = request.POST.get("value", "").strip()
		try:
			warning = mapmeta.set_parameter(key, value)
			self.message_user(request, "Set {} to {}.".format(key, value))
			if warning:
				self.message_user(request, warning, level=messages.WARNING)
		except mapmeta.ParameterError as err:
			self.message_user(request, str(err), level=messages.ERROR)
		except Exception as err:
			self.message_user(request, "Could not set {}: {}".format(key, err), level=messages.ERROR)
		return self.changelist_redirect()

	@method_decorator(csrf_protect)
	@method_decorator(require_POST)
	def remove_view(self, request):
		"""Remove one parameter, so that its default applies again."""
		if not self.may_edit(request):
			raise PermissionDenied
		key = request.POST.get("key", "").strip()
		try:
			if mapmeta.remove_parameter(key):
				self.message_user(request, "Removed {}.".format(key))
			else:
				self.message_user(request, "The parameter {} was not set.".format(key), level=messages.WARNING)
		except mapmeta.ParameterError as err:
			self.message_user(request, str(err), level=messages.ERROR)
		except Exception as err:
			self.message_user(request, "Could not remove {}: {}".format(key, err), level=messages.ERROR)
		return self.changelist_redirect()
