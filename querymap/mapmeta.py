"""The map's metadata parameters: settings pgmap keeps in the map database.

Every program using the map reads them from there, the pgmap command line
tools as well as this server, so they need setting in one place only.
"""
import re

import pgmap
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap

class Known:
	def __init__(self, description, default=None, switch=False, protected=False):
		self.description = description
		self.default = default # What applies when the parameter is not set
		self.switch = switch # Takes only 0 or 1
		self.protected = protected # Shown, but not changed from here

KNOWN = {
	"schema_version": Known("The layout of the map tables. Only pgmap's admin tool changes it, "
		"when it upgrades or downgrades them.", protected=True),
	"useBboxInQuery": Known("1: find ways and relations in an area by the bounding box stored with "
		"each, which is quicker and also finds those that cross the area without a node in it. "
		"Every way and relation must have its box stored first, with \"Update way/relation bboxes\" "
		"in pgmap's admin tool. 0: find them through their nodes.", default="0", switch=True),
	"readonly": Known("1: refuse every change to the map. 0: allow editing.", default="0", switch=True),
}

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
VALUE_MAXIMUM = 1000

class ParameterError(ValueError):
	"""A parameter that cannot be set or removed as asked."""

def list_parameters():
	"""The parameters of the tables being edited, and of the static tables under them.

	Each is a dict with key, value and, for parameters known to this module,
	description, default, switch and protected. Known parameters that are not
	set are listed with a value of None.
	"""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		active, static = pgmap.mapstringstring(), pgmap.mapstringstring()
		t.GetMetaValues(active)
		t.GetMetaValues(static, True)
		active, static = dict(active), dict(static)
	finally:
		common.abort_transaction(t)

	def describe(key, value):
		known = KNOWN.get(key)
		return {
			"key": key,
			"value": value,
			"description": known.description if known else "",
			"default": known.default if known else None,
			"switch": bool(known and known.switch),
			"protected": bool(known and known.protected),
		}
	keys = sorted(set(active) | set(k for k, known in KNOWN.items() if not known.protected))
	return ([describe(key, active.get(key)) for key in keys],
		[describe(key, static[key]) for key in sorted(static)])

def check_parameter(key, value=None):
	"""Raise ParameterError unless the key, and the value if given, may be stored."""
	if not KEY_PATTERN.match(key):
		raise ParameterError("A parameter name is up to 100 letters, digits and the characters _ . : -")
	known = KNOWN.get(key)
	if known is not None and known.protected:
		raise ParameterError("The parameter {} cannot be changed here.".format(key))
	if value is None:
		return
	if len(value) > VALUE_MAXIMUM:
		raise ParameterError("A value is at most {} characters.".format(VALUE_MAXIMUM))
	if known is not None and known.switch and value not in ("0", "1"):
		raise ParameterError("The parameter {} must be 0 or 1.".format(key))

def set_parameter(key, value):
	"""Store a parameter. Returns a warning to show, or None."""
	check_parameter(key, value)
	t = get_pgmap().GetTransaction("EXCLUSIVE")
	try:
		error = pgmap.PgMapError()
		if not t.SetMetaValue(key, value, error):
			raise ParameterError(error.errStr or "The map database refused the change.")
		warning = None
		if key == "useBboxInQuery" and value == "1":
			total, missing = list(t.CountWaysWithoutBbox())
			if missing > 0:
				warning = ("{} of the map's {} ways have no bounding box stored, so searches by area "
					"will not find them. Run \"Update way/relation bboxes\" in pgmap's admin tool, "
					"or set useBboxInQuery back to 0.".format(missing, total))
		t.Commit()
		return warning
	except BaseException:
		common.abort_transaction(t)
		raise

def remove_parameter(key):
	"""Remove a parameter, so that its default applies. Returns whether it was set."""
	check_parameter(key)
	t = get_pgmap().GetTransaction("EXCLUSIVE")
	try:
		removed = t.DeleteMetaValue(key)
		t.Commit()
		return bool(removed)
	except BaseException:
		common.abort_transaction(t)
		raise
