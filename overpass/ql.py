# -*- coding: utf-8 -*-
"""Parser for the subset of Overpass QL this server understands.

The result is a tree of the classes below, which are named after the elements
of Overpass XML: QL and XML are two ways of writing the same statements, so a
reader for the XML form would produce the same tree.

Anything outside the subset is refused, naming the feature where it is
recognised, because quietly ignoring part of a query returns wrong data.
"""
from __future__ import unicode_literals
import re

class QueryError(Exception):
	"""A query that cannot be run. status is the HTTP status to answer with."""
	status = 400

	def __init__(self, message, line=None):
		self.line = line
		if line is not None:
			message = "line {}: {}".format(line, message)
		super(QueryError, self).__init__(message)

class UnsupportedFeature(QueryError):
	"""Valid Overpass QL that this server does not implement."""
	def __init__(self, feature, line=None):
		self.feature = feature
		super(UnsupportedFeature, self).__init__(
			"{} not supported by this server".format(feature), line)

# ****** The statement tree ******

DEFAULT_SET = "_"

class Node(object):
	"""Base of the tree classes: compared and printed by their fields."""
	fields = ()

	def __eq__(self, other):
		return type(self) is type(other) and all(
			getattr(self, f) == getattr(other, f) for f in self.fields)

	def __ne__(self, other):
		return not self == other

	def __repr__(self):
		return "{}({})".format(type(self).__name__,
			", ".join("{}={!r}".format(f, getattr(self, f)) for f in self.fields))

class OsmScript(Node):
	"""A whole query: its settings and statements."""
	fields = ("output", "timeout", "maxsize", "bbox", "bbox_from_url", "statements")

	def __init__(self, statements, output="xml", timeout=None, maxsize=None, bbox=None,
		bbox_from_url=False):
		self.statements = statements
		self.output = output # "xml" or "json"
		self.timeout = timeout # Seconds, or None if not given
		self.maxsize = maxsize
		self.bbox = bbox # None or (south, west, north, east)
		self.bbox_from_url = bbox_from_url # [bbox] with the area in the bbox URL parameter

class Query(Node):
	"""node, way, rel, nwr and so on, with filters."""
	fields = ("types", "filters", "into")

	def __init__(self, types, filters, into=DEFAULT_SET):
		self.types = tuple(types) # Of "node", "way", "relation"
		self.filters = list(filters)
		self.into = into

class HasKv(Node):
	"""A tag filter. mode is one of the constants below."""
	EXISTS, NOT_EXISTS, EQUALS, NOT_EQUALS, MATCHES, NOT_MATCHES = (
		"exists", "not_exists", "equals", "not_equals", "matches", "not_matches")
	fields = ("mode", "key", "value", "ignore_case")

	def __init__(self, mode, key, value="", ignore_case=False):
		self.mode = mode
		self.key = key
		self.value = value
		self.ignore_case = ignore_case

class BboxQuery(Node):
	fields = ("south", "west", "north", "east")

	def __init__(self, south, west, north, east):
		self.south, self.west, self.north, self.east = south, west, north, east

class IdQuery(Node):
	fields = ("ids",)

	def __init__(self, ids):
		self.ids = tuple(ids)

class ItemFilter(Node):
	"""Restricts a query to the members of a set."""
	fields = ("set",)

	def __init__(self, set):
		self.set = set

class Item(Node):
	"""A set used as a statement, as in (._; >;)."""
	fields = ("set", "into")

	def __init__(self, set=DEFAULT_SET, into=None):
		self.set = set
		self.into = into # None leaves every set as it is

class Union(Node):
	fields = ("statements", "into")

	def __init__(self, statements, into=DEFAULT_SET):
		self.statements = list(statements)
		self.into = into

class Recurse(Node):
	"""type is "down" (>), "down-rel" (>>), "up" (<) or "up-rel" (<<)."""
	fields = ("type", "from_set", "into")

	def __init__(self, type, from_set=DEFAULT_SET, into=DEFAULT_SET):
		self.type = type
		self.from_set = from_set
		self.into = into

class Print(Node):
	"""An out statement."""
	fields = ("from_set", "mode", "geometry", "order", "limit")

	def __init__(self, from_set=DEFAULT_SET, mode="body", geometry=None, order="asc", limit=None):
		self.from_set = from_set
		self.mode = mode # "ids", "skel", "body", "tags", "meta" or "count"
		self.geometry = geometry # None, "geom", "bb" or "center"
		self.order = order # "asc" or "qt"
		self.limit = limit

# ****** Parsing ******

TYPE_SPECIFIERS = {
	"node": ("node",),
	"way": ("way",),
	"rel": ("relation",),
	"relation": ("relation",),
	"nwr": ("node", "way", "relation"),
	"nw": ("node", "way"),
	"nr": ("node", "relation"),
	"wr": ("way", "relation"),
}

UNSUPPORTED_STATEMENTS = {
	"area": "area queries are",
	"derived": "derived elements are",
	"is_in": "is_in is",
	"map_to_area": "map_to_area is",
	"foreach": "foreach loops are",
	"for": "for loops are",
	"if": "if statements are",
	"else": "if statements are",
	"complete": "complete loops are",
	"make": "make statements are",
	"convert": "convert statements are",
	"retro": "retro blocks are",
	"compare": "compare statements are",
	"timeline": "timeline statements are",
	"local": "local statements are",
}

UNSUPPORTED_FILTERS = {
	"around": "around filters are",
	"poly": "poly filters are",
	"area": "area filters are",
	"pivot": "pivot filters are",
	"user": "user filters are",
	"uid": "uid filters are",
	"user_touched": "user_touched filters are",
	"uid_touched": "uid_touched filters are",
	"newer": "newer filters are",
	"changed": "changed filters are",
	"if": "if: filters are",
	"r": "recurse filters such as (r) are",
	"w": "recurse filters such as (w) are",
	"bn": "recurse filters such as (bn) are",
	"bw": "recurse filters such as (bw) are",
	"br": "recurse filters such as (br) are",
	"way_link": "way_link filters are",
	"way_cnt": "way_cnt filters are",
}

UNSUPPORTED_SETTINGS = {
	"date": "the date setting is",
	"diff": "the diff setting is",
	"adiff": "the adiff setting is",
}

RECURSE_SYMBOLS = (("<<", "up-rel"), (">>", "down-rel"), ("<", "up"), (">", "down"))

XML_QUERY = re.compile(r"\s*<\s*(\?xml|osm-script|query|union|difference|print|bbox-query|"
	r"id-query|recurse|item|foreach|around|area-query|user|newer|has-kv)\b")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BARE_WORD = re.compile(r"[A-Za-z0-9_]+")
NUMBER = re.compile(r"[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?)")
INTEGER = re.compile(r"\d+")
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}

class Parser(object):
	def __init__(self, text):
		self.text = text
		self.pos = 0

	# *** Helpers ***

	def line(self):
		return self.text.count("\n", 0, self.pos) + 1

	def error(self, message):
		return QueryError(message, self.line())

	def unsupported(self, feature):
		return UnsupportedFeature(feature, self.line())

	def skip(self):
		"""Move past whitespace and comments."""
		text = self.text
		while self.pos < len(text):
			if text[self.pos].isspace():
				self.pos += 1
			elif text.startswith("//", self.pos):
				end = text.find("\n", self.pos)
				self.pos = len(text) if end < 0 else end
			elif text.startswith("/*", self.pos):
				end = text.find("*/", self.pos + 2)
				if end < 0:
					raise self.error("comment is not closed")
				self.pos = end + 2
			else:
				break

	def at_end(self):
		self.skip()
		return self.pos >= len(self.text)

	def peek(self, symbol):
		self.skip()
		return self.text.startswith(symbol, self.pos)

	def accept(self, symbol):
		if self.peek(symbol):
			self.pos += len(symbol)
			return True
		return False

	def found(self):
		"""What is at the current position, for error messages."""
		if self.at_end():
			return "the end of the query"
		return '"{}"'.format(self.text[self.pos:self.pos + 12].split("\n")[0])

	def expect(self, symbol):
		if not self.accept(symbol):
			raise self.error('expected "{}" but found {}'.format(symbol, self.found()))

	def match(self, pattern):
		self.skip()
		m = pattern.match(self.text, self.pos)
		if m is None:
			return None
		self.pos = m.end()
		return m.group(0)

	def peek_word(self):
		self.skip()
		m = IDENTIFIER.match(self.text, self.pos)
		return None if m is None else m.group(0)

	def string(self):
		"""A quoted string, or None if there is not one here."""
		self.skip()
		if self.pos >= len(self.text) or self.text[self.pos] not in "\"'":
			return None
		quote = self.text[self.pos]
		self.pos += 1
		out = []
		while True:
			if self.pos >= len(self.text):
				raise self.error("string is not closed")
			ch = self.text[self.pos]
			self.pos += 1
			if ch == quote:
				return "".join(out)
			if ch != "\\":
				out.append(ch)
				continue
			if self.pos >= len(self.text):
				raise self.error("string is not closed")
			ch = self.text[self.pos]
			self.pos += 1
			if ch == "u":
				digits = self.text[self.pos:self.pos + 4]
				if not re.match(r"^[0-9A-Fa-f]{4}$", digits):
					raise self.error("\\u must be followed by four hexadecimal digits")
				out.append(chr(int(digits, 16)))
				self.pos += 4
			else:
				# An unknown escape stands for the character itself
				out.append(ESCAPES.get(ch, ch))

	def text_value(self, what):
		"""A tag key or value: quoted, or a bare word of letters, digits and underscores."""
		value = self.string()
		if value is None:
			value = self.match(BARE_WORD)
		if value is None:
			raise self.error("expected {} but found {}".format(what, self.found()))
		return value

	def number(self):
		value = self.match(NUMBER)
		if value is None:
			raise self.error("expected a number but found {}".format(self.found()))
		return float(value)

	def set_name(self):
		"""The name after the dot of a set reference."""
		name = self.match(IDENTIFIER)
		if name is None:
			raise self.error("expected a set name but found {}".format(self.found()))
		return name

	def into(self, default=DEFAULT_SET):
		"""An optional ->.name redirection."""
		if not self.accept("->"):
			return default
		self.expect(".")
		return self.set_name()

	def bbox(self, values):
		south, west, north, east = values
		if not (-90 <= south <= 90 and -90 <= north <= 90 and
			-180 <= west <= 180 and -180 <= east <= 180):
			raise self.error("bounding box coordinates are out of range; the order is south,west,north,east")
		if south > north:
			raise self.error("bounding box south is greater than north; the order is south,west,north,east")
		if west > east:
			raise self.unsupported("bounding boxes that cross the antimeridian (west greater than east) are")
		return (south, west, north, east)

	# *** Grammar ***

	def parse(self):
		if "{{" in self.text:
			self.pos = self.text.index("{{")
			raise self.error("{{...}} is an Overpass Turbo shortcut, which Overpass Turbo "
				"replaces before sending the query; it is not part of Overpass QL")
		if XML_QUERY.match(self.text):
			raise self.unsupported("Overpass XML queries are; use Overpass QL, as they are")

		script = OsmScript([])
		if self.peek("["):
			self.settings(script)
			self.expect(";")
		while not self.at_end():
			script.statements.append(self.statement(False))
		if len(script.statements) == 0:
			raise QueryError("the query contains no statements")
		return script

	def settings(self, script):
		seen = set()
		while self.accept("["):
			name = self.match(IDENTIFIER)
			if name is None:
				raise self.error("expected a setting name but found {}".format(self.found()))
			if name in UNSUPPORTED_SETTINGS:
				raise self.unsupported(UNSUPPORTED_SETTINGS[name])
			if name in seen:
				raise self.error("the {} setting is given twice".format(name))
			seen.add(name)

			if name == "bbox" and self.accept("]"):
				script.bbox_from_url = True
				continue
			self.expect(":")
			if name == "out":
				value = self.match(IDENTIFIER)
				if value in ("csv", "custom", "popup"):
					raise self.unsupported("out:{} output is".format(value))
				if value not in ("xml", "json"):
					raise self.error("the out setting must be xml or json")
				script.output = value
			elif name in ("timeout", "maxsize"):
				value = self.match(INTEGER)
				if value is None:
					raise self.error("the {} setting must be a whole number".format(name))
				setattr(script, name, int(value))
			elif name == "bbox":
				values = [self.number()]
				for i in range(3):
					self.expect(",")
					values.append(self.number())
				script.bbox = self.bbox(values)
			else:
				raise self.error("unknown setting {}".format(name))
			self.expect("]")

	def statement(self, in_union):
		self.skip()
		if self.accept("("):
			return self.union()

		from_set = None
		if self.accept("."):
			from_set = self.set_name()

		for symbol, kind in RECURSE_SYMBOLS:
			if self.accept(symbol):
				statement = Recurse(kind, from_set or DEFAULT_SET, self.into())
				self.expect(";")
				return statement

		word = self.peek_word()
		if word == "out":
			if in_union:
				raise self.error("out cannot be used inside a union")
			self.pos += len(word)
			return self.print_statement(from_set or DEFAULT_SET)

		if from_set is not None:
			if word in UNSUPPORTED_STATEMENTS:
				raise self.unsupported(UNSUPPORTED_STATEMENTS[word])
			statement = Item(from_set, self.into(None))
			self.expect(";")
			return statement

		if word in TYPE_SPECIFIERS:
			self.pos += len(word)
			return self.query(TYPE_SPECIFIERS[word])
		if word in UNSUPPORTED_STATEMENTS:
			raise self.unsupported(UNSUPPORTED_STATEMENTS[word])
		if word is not None:
			raise self.error('unknown statement "{}"'.format(word))
		raise self.error("expected a statement but found {}".format(self.found()))

	def union(self):
		statements = []
		while not self.accept(")"):
			if self.at_end():
				raise self.error("union is not closed")
			if self.peek("-"):
				raise self.unsupported("difference statements, written (a; - b;), are")
			statements.append(self.statement(True))
		if len(statements) == 0:
			raise self.error("union is empty")
		statement = Union(statements, self.into())
		self.expect(";")
		return statement

	def query(self, types):
		filters = []
		while True:
			if self.accept("["):
				filters.append(self.tag_filter())
			elif self.accept("("):
				filters.append(self.round_filter())
			elif self.peek(".") :
				self.pos += 1
				filters.append(ItemFilter(self.set_name()))
			else:
				break
		statement = Query(types, filters, self.into())
		self.expect(";")
		return statement

	def tag_filter(self):
		if self.peek("~"):
			raise self.unsupported("tag filters with a regular expression for the key are")
		if self.accept("!"):
			key = self.text_value("a tag key")
			self.expect("]")
			return HasKv(HasKv.NOT_EXISTS, key)
		key = self.text_value("a tag key")
		if self.accept("]"):
			return HasKv(HasKv.EXISTS, key)

		if self.accept("!="):
			mode = HasKv.NOT_EQUALS
		elif self.accept("!~"):
			mode = HasKv.NOT_MATCHES
		elif self.accept("~"):
			mode = HasKv.MATCHES
		elif self.accept("=="):
			mode = HasKv.EQUALS
		elif self.accept("="):
			mode = HasKv.EQUALS
		else:
			raise self.error("expected a tag filter operator or ] but found {}".format(self.found()))
		value = self.text_value("a tag value")
		ignore_case = False
		if mode in (HasKv.MATCHES, HasKv.NOT_MATCHES) and self.accept(","):
			if self.match(IDENTIFIER) != "i":
				raise self.error('the only option for a regular expression is "i"')
			ignore_case = True
		self.expect("]")
		return HasKv(mode, key, value, ignore_case)

	def round_filter(self):
		word = self.peek_word()
		if word == "id":
			self.pos += len(word)
			self.expect(":")
			ids = [self.object_id()]
			while self.accept(","):
				ids.append(self.object_id())
			self.expect(")")
			return IdQuery(ids)
		if word in UNSUPPORTED_FILTERS:
			raise self.unsupported(UNSUPPORTED_FILTERS[word])
		if word is not None:
			raise self.error('unknown filter "{}"'.format(word))

		start = self.pos
		values = [self.number()]
		while self.accept(","):
			values.append(self.number())
		self.expect(")")
		if len(values) == 4:
			return BboxQuery(*self.bbox(values))
		if len(values) == 1:
			self.pos = start
			ids = [self.object_id()]
			self.expect(")")
			return IdQuery(ids)
		raise self.error("expected one ID or the four coordinates south,west,north,east in the brackets")

	def object_id(self):
		value = self.match(INTEGER)
		if value is None or len(value) > 18:
			raise self.error("expected an object ID but found {}".format(self.found()))
		return int(value)

	def print_statement(self, from_set):
		statement = Print(from_set)
		given = set()

		def choose(group, value):
			if group in given:
				raise self.error('"{}" conflicts with an earlier option of out'.format(value))
			given.add(group)

		while not self.accept(";"):
			if self.peek("("):
				raise self.unsupported("bounding boxes on out statements are")
			number = self.match(INTEGER)
			if number is not None:
				choose("limit", number)
				statement.limit = int(number)
				continue
			word = self.match(IDENTIFIER)
			if word in ("ids", "skel", "body", "tags", "meta", "count"):
				choose("mode", word)
				statement.mode = word
			elif word in ("geom", "bb", "center"):
				choose("geometry", word)
				statement.geometry = word
			elif word in ("asc", "qt"):
				choose("order", word)
				statement.order = word
			elif word == "noids":
				raise self.unsupported("the noids option of out is")
			elif word is None:
				raise self.error('expected an option of out or ";" but found {}'.format(self.found()))
			else:
				raise self.error('unknown option "{}" of out'.format(word))
		if statement.mode == "count" and (statement.geometry is not None or statement.limit is not None):
			raise self.error("out count cannot be combined with other options")
		return statement

def parse(text):
	"""Parse a query, returning an OsmScript or raising QueryError."""
	return Parser(text).parse()
