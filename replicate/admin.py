from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from .extracts import list_db_extracts, get_db_extract, save_db_extract, change_db_extract
from .models import DbExtract

class DbExtractForm(forms.Form):
	name = forms.CharField(required=False, max_length=255,
		help_text="Optional. Must be unique, so the extract can be selected by name.")
	west = forms.FloatField(min_value=-180, max_value=180, label="West (minimum longitude)")
	south = forms.FloatField(min_value=-90, max_value=90, label="South (minimum latitude)")
	east = forms.FloatField(min_value=-180, max_value=180, label="East (maximum longitude)")
	north = forms.FloatField(min_value=-90, max_value=90, label="North (maximum latitude)")

	def clean_name(self):
		return self.cleaned_data["name"].strip()

	def clean(self):
		data = super().clean()
		if None not in (data.get("west"), data.get("east")) and data["west"] >= data["east"]:
			self.add_error("east", "East must be greater than west.")
		if None not in (data.get("south"), data.get("north")) and data["south"] >= data["north"]:
			self.add_error("north", "North must be greater than south.")
		return data

	def bbox(self):
		return [self.cleaned_data[key] for key in ("west", "south", "east", "north")]

@admin.register(DbExtract)
class DbExtractAdmin(admin.ModelAdmin):
	"""Read-only list of stored extracts, read through pgmap."""

	def has_add_permission(self, request):
		# Saving an extract runs a map query of the whole bbox, so it is not
		# open to every staff user.
		user = request.user
		return user.is_active and user.is_staff and (
			user.is_superuser or user.has_perm("replicate.add_dbextract"))

	# The stock change and delete views use the ORM, which has no table to read,
	# so they stay disabled. Updating and deleting have their own views below.
	def has_change_permission(self, request, obj=None):
		return False

	def has_delete_permission(self, request, obj=None):
		return False

	def has_extract_permission(self, request, action):
		user = request.user
		return user.is_active and user.is_staff and (
			user.is_superuser or user.has_perm("replicate.{}_dbextract".format(action)))

	def get_urls(self):
		view = self.admin_site.admin_view
		# Listed first so they take precedence over the stock object routes.
		return [
			path("<int:extract_id>/", view(self.detail_view), name="replicate_dbextract_detail"),
			path("<int:extract_id>/update/", view(self.update_view), name="replicate_dbextract_update"),
			path("<int:extract_id>/delete/", view(self.remove_view), name="replicate_dbextract_remove"),
		] + super().get_urls()

	def has_view_permission(self, request, obj=None):
		return request.user.is_active and request.user.is_staff

	def has_module_permission(self, request):
		return self.has_view_permission(request)

	def changelist_view(self, request, extra_context=None):
		context = dict(self.admin_site.each_context(request))
		context.update({
			"title": "Database extracts",
			"opts": self.model._meta,
			"extracts": [],
			"error": None,
			"has_add_permission": self.has_add_permission(request),
			"has_update_permission": self.has_extract_permission(request, "change"),
			"has_remove_permission": self.has_extract_permission(request, "delete"),
		})
		try:
			context["extracts"] = list_db_extracts()
		except Exception as err:
			context["error"] = str(err)
		context.update(extra_context or {})
		return TemplateResponse(request, "admin/replicate/dbextract/change_list.html", context)

	@method_decorator(csrf_protect)
	def add_view(self, request, form_url="", extra_context=None):
		if not self.has_add_permission(request):
			raise PermissionDenied
		form = DbExtractForm(request.POST or None)
		if request.method == "POST" and form.is_valid():
			name = form.cleaned_data["name"]
			try:
				if name and any(extract["name"] == name for extract in list_db_extracts()):
					form.add_error("name", "An extract with this name already exists.")
				else:
					# Runs in this request; a large bbox takes a long time.
					extract_id = save_db_extract(form.bbox(), name)
					self.message_user(request, "Saved database extract {}.".format(extract_id))
					return HttpResponseRedirect(reverse("admin:replicate_dbextract_changelist"))
			except Exception as err:
				form.add_error(None, "Could not save extract: {}".format(err))
		context = dict(self.admin_site.each_context(request))
		context.update({
			"title": "Add database extract",
			"opts": self.model._meta,
			"form": form,
		})
		context.update(extra_context or {})
		return TemplateResponse(request, "admin/replicate/dbextract/add_form.html", context)

	def changelist_redirect(self):
		return HttpResponseRedirect(reverse("admin:replicate_dbextract_changelist"))

	@method_decorator(csrf_protect)
	@method_decorator(require_POST)
	def update_view(self, request, extract_id):
		"""Bring one extract up to date with the map."""
		if not self.has_extract_permission(request, "change"):
			raise PermissionDenied
		try:
			# Runs in this request; a large extract takes a long time.
			change_db_extract("UpdateExtract", extract_id)
			self.message_user(request, "Updated database extract {}.".format(extract_id))
		except Exception as err:
			self.message_user(request, "Could not update extract {}: {}".format(extract_id, err),
				level=messages.ERROR)
		return self.changelist_redirect()

	@method_decorator(csrf_protect)
	def remove_view(self, request, extract_id):
		"""Confirm, then delete one extract. The map itself is not changed."""
		if not self.has_extract_permission(request, "delete"):
			raise PermissionDenied
		if request.method == "POST":
			try:
				change_db_extract("DeleteExtract", extract_id)
				self.message_user(request, "Deleted database extract {}.".format(extract_id))
			except Exception as err:
				self.message_user(request, "Could not delete extract {}: {}".format(extract_id, err),
					level=messages.ERROR)
			return self.changelist_redirect()
		extract = get_db_extract(extract_id)
		if extract is None:
			raise Http404("Extract not found")
		context = dict(self.admin_site.each_context(request))
		context.update({
			"title": "Delete database extract",
			"opts": self.model._meta,
			"extract": extract,
		})
		return TemplateResponse(request, "admin/replicate/dbextract/delete_confirmation.html", context)

	def detail_view(self, request, extract_id):
		"""Show one extract, including its object counts."""
		if not self.has_view_permission(request):
			raise PermissionDenied
		extract = get_db_extract(extract_id)
		if extract is None:
			raise Http404("Extract not found")
		context = dict(self.admin_site.each_context(request))
		context.update({
			"title": "Database extract {}".format(extract_id),
			"opts": self.model._meta,
			"extract": extract,
			"has_update_permission": self.has_extract_permission(request, "change"),
			"has_remove_permission": self.has_extract_permission(request, "delete"),
		})
		return TemplateResponse(request, "admin/replicate/dbextract/detail.html", context)
