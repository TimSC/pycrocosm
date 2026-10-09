# -*- coding: utf-8 -*-
"""Runs a parsed Overpass query against the map database.

The state of a running query is a collection of named sets of elements, as in
Overpass. Elements are copied out of pgmap into plain Python objects as they
are read, so nothing here depends on the lifetime of a pgmap container.
"""
from __future__ import unicode_literals
import math
import time

import pgmap
from . import ql, output
from .ql import QueryError, HasKv

KINDS = ("node", "way", "relation")
# Ways or relations to find by their tags before checking which are in a
# bounding box; with more than this, the search starts from the box instead.
TAG_FIRST_MAXIMUM = 20000
# A region with no more nodes than this is searched by working up from them,
# even when the other filters could be searched for first.
REGION_FIRST_MAXIMUM = 2000
# Positions an around filter may measure from
AROUND_POSITIONS_MAXIMUM = 5000

class QueryTimeout(QueryError):
	status = 504

	def __init__(self, seconds):
		super(QueryTimeout, self).__init__(
			"the query did not finish within its timeout of {} seconds".format(seconds))

class TooManyElements(QueryError):
	def __init__(self, maximum):
		super(TooManyElements, self).__init__(
			"the query selects more than {} elements, the most this server allows; "
			"use a smaller area or more specific tags".format(maximum))

class Limits(object):
	"""What a query may use. See overpass.interpreter.get_limits."""
	def __init__(self, timeout, max_elements, area_maximum):
		self.timeout = timeout # Seconds
		self.max_elements = max_elements
		self.area_maximum = area_maximum # Square degrees, for a bbox that is the only selective filter

class Element(object):
	__slots__ = ("kind", "id", "lat", "lon", "tags", "refs", "members",
		"version", "timestamp", "changeset", "uid", "user")

	def __init__(self, kind, obj):
		meta = obj.metaData
		self.kind = kind
		self.id = obj.objId
		self.tags = dict(obj.tags)
		self.version = meta.version
		self.timestamp = meta.timestamp
		self.changeset = meta.changeset
		self.uid = meta.uid
		self.user = meta.username
		self.lat = self.lon = None
		self.refs = self.members = None
		if kind == "node":
			self.lat, self.lon = obj.lat, obj.lon
		elif kind == "way":
			self.refs = list(obj.refs)
		else:
			self.members = [(m.TypeName(), m.ref, m.role) for m in obj.members]

def elements_of(data):
	"""Copy the contents of a pgmap.OsmData, which must stay alive meanwhile."""
	out = []
	for kind, container in (("node", data.nodes), ("way", data.ways), ("relation", data.relations)):
		for i in range(len(container)):
			out.append(Element(kind, container[i]))
	return out

def new_set():
	return {"node": {}, "way": {}, "relation": {}}

def set_size(elements):
	return sum(len(elements[kind]) for kind in KINDS)

def add_to_set(target, elements):
	for element in elements:
		target[element.kind][element.id] = element

FILTER_OPS = {
	HasKv.EXISTS: pgmap.OverpassTagFilter.Exists,
	HasKv.NOT_EXISTS: pgmap.OverpassTagFilter.NotExists,
	HasKv.EQUALS: pgmap.OverpassTagFilter.Equals,
	HasKv.NOT_EQUALS: pgmap.OverpassTagFilter.NotEquals,
	HasKv.MATCHES: pgmap.OverpassTagFilter.Matches,
	HasKv.NOT_MATCHES: pgmap.OverpassTagFilter.NotMatches,
}

class MapSource(object):
	"""Reads elements through a pgmap transaction, within the query's limits."""
	def __init__(self, transaction, limits):
		self.transaction = transaction
		self.limits = limits
		self.deadline = time.time() + limits.timeout
		self.bbox_column = None

	def stores_bboxes(self):
		"""Whether the map stores a bounding box for every way and relation, so
		that they can be searched for by area directly."""
		if self.bbox_column is None:
			self.bbox_column = bool(self.transaction.UseBboxInQuery())
		return self.bbox_column

	def _guarded(self, call):
		"""Make a database call with what is left of the query's time."""
		remaining = self.deadline - time.time()
		if remaining <= 0:
			raise QueryTimeout(self.limits.timeout)
		self.transaction.SetStatementTimeout(max(1, int(remaining * 1000)))
		try:
			return call()
		except (RuntimeError, ValueError, SystemError) as err:
			message = str(err)
			if "Query timed out" in message:
				raise QueryTimeout(self.limits.timeout)
			if "Invalid regular expression" in message:
				raise QueryError("a regular expression in the query is not valid")
			raise

	def _run(self, call):
		data = pgmap.OsmData()
		self._guarded(lambda: call(data))
		return elements_of(data)

	@staticmethod
	def _arguments(conditions, region, ids):
		"""The pgmap arguments for a search: conditions are the tag, user and
		time filters of a query, region is None or a Region."""
		filters = pgmap.vectoroverpasstagfilter()
		options = pgmap.OverpassQueryOptions()
		for f in conditions:
			if isinstance(f, HasKv):
				filters.append(pgmap.OverpassTagFilter(FILTER_OPS[f.mode], f.key, f.value, f.ignore_case))
			elif isinstance(f, ql.User):
				options.uids = pgmap.vectori64(f.uids)
				options.usernames = pgmap.vectorstring(f.names)
			elif isinstance(f, ql.Newer):
				options.newerThan = max(options.newerThan, f.than)
			elif isinstance(f, ql.Changed):
				options.changedSince = max(options.changedSince, f.since)
				if f.until is not None:
					options.changedUntil = f.until if options.changedUntil < 0 else min(options.changedUntil, f.until)
		area = []
		if region is not None:
			if region.bbox is not None:
				south, west, north, east = region.bbox
				area = [west, south, east, north]
			if region.around is not None:
				radius, points = region.around
				options.aroundRadius = radius
				options.aroundPoints = pgmap.vectord([v for lat, lon in points for v in (lon, lat)])
		return filters, area, sorted(ids or ()), options

	def query(self, kind, conditions, region, ids, maximum=None):
		"""Elements of one kind meeting the conditions. region is None or a
		Region; for ways and relations it must be a bounding box alone, on a map
		that stores their boxes. ids is None or the IDs to look among.
		Finding more than maximum, by default the most a query may select, is an error."""
		if ids is not None and len(ids) == 0:
			return []
		if maximum is None:
			maximum = self.limits.max_elements
		filters, area, id_list, options = self._arguments(conditions, region, ids)
		found = self._run(lambda data: self.transaction.OverpassQuery(
			kind, filters, area, id_list, maximum + 1, data, options))
		if len(found) > maximum:
			raise TooManyElements(self.limits.max_elements)
		return found

	def query_ids(self, kind, conditions, region, ids, maximum):
		"""As query, but only the IDs of what is found, as a set."""
		if ids is not None and len(ids) == 0:
			return set()
		filters, area, id_list, options = self._arguments(conditions, region, ids)
		found = pgmap.vectori64()
		self._guarded(lambda: self.transaction.OverpassQueryIds(
			kind, filters, area, id_list, maximum + 1, found, options))
		if len(found) > maximum:
			raise TooManyElements(self.limits.max_elements)
		return set(found)

	def by_ids(self, kind, ids):
		if len(ids) == 0:
			return []
		# The callers limit what they ask for; this is a backstop
		if len(ids) > 10 * self.limits.max_elements:
			raise TooManyElements(self.limits.max_elements)
		return self._run(lambda data: self.transaction.GetObjectsById(kind, sorted(ids), data))

	def ways_of_nodes(self, ids):
		if len(ids) == 0:
			return []
		return self._run(lambda data: self.transaction.GetWaysForNodes(sorted(ids), data))

	def relations_of(self, kind, ids):
		if len(ids) == 0:
			return []
		return self._run(lambda data: self.transaction.GetRelationsForObjs(kind, sorted(ids), data))

def intersect_bboxes(boxes):
	"""The area common to (south, west, north, east) boxes: None for no boxes, False if empty."""
	if len(boxes) == 0:
		return None
	south = max(b[0] for b in boxes)
	west = max(b[1] for b in boxes)
	north = min(b[2] for b in boxes)
	east = min(b[3] for b in boxes)
	if south > north or west > east:
		return False
	return (south, west, north, east)

METRES_PER_DEGREE = 111000.0

class Region(object):
	"""Where a query looks: a bounding box, a distance from some positions, or both."""
	def __init__(self, bbox=None, around=None):
		self.bbox = bbox # None or (south, west, north, east)
		self.around = around # None or (radius in metres, tuple of (lat, lon))

	def key(self):
		return (self.bbox, self.around)

	def area(self):
		"""Roughly how much is searched, in square degrees."""
		areas = []
		if self.bbox is not None:
			areas.append((self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1]))
		if self.around is not None:
			radius, points = self.around
			side = 2.0 * radius / METRES_PER_DEGREE
			areas.append(sum(side * side / max(0.01, math.cos(math.radians(lat))) for lat, lon in points))
		return min(areas)

class Evaluator(object):
	def __init__(self, source, limits):
		self.source = source
		self.limits = limits
		self.sets = {}
		self.global_bbox = None
		self.output = [] # (Print statement, elements in output order, or counts by kind)
		self.output_size = 0
		self.region_cache = {}

	def run(self, script):
		self.global_bbox = script.bbox
		for statement in script.statements:
			self.execute(statement)
		return self.output

	def get_set(self, name):
		return self.sets.get(name) or new_set()

	def store(self, name, elements):
		if set_size(elements) > self.limits.max_elements:
			raise TooManyElements(self.limits.max_elements)
		if name is not None:
			self.sets[name] = elements
		return elements

	def execute(self, statement):
		"""Run one statement, returning its result set."""
		if isinstance(statement, ql.Query):
			return self.store(statement.into, self.query(statement))
		if isinstance(statement, ql.Union):
			result = new_set()
			for child in statement.statements:
				part = self.execute(child)
				for kind in KINDS:
					result[kind].update(part[kind])
			return self.store(statement.into, result)
		if isinstance(statement, ql.Difference):
			first = self.execute(statement.first)
			second = self.execute(statement.second)
			result = dict((kind, dict((i, e) for i, e in first[kind].items() if i not in second[kind]))
				for kind in KINDS)
			return self.store(statement.into, result)
		if isinstance(statement, ql.Item):
			return self.store(statement.into, self.get_set(statement.set))
		if isinstance(statement, ql.Recurse):
			return self.store(statement.into, self.recurse(statement.type, self.get_set(statement.from_set)))
		if isinstance(statement, ql.Print):
			self.print_set(statement)
			return new_set()
		raise QueryError("statement cannot be run")

	# *** Queries ***

	def query(self, statement):
		filters = statement.filters
		conditions = [f for f in filters if isinstance(f, (HasKv, ql.User, ql.Newer, ql.Changed))]
		if sum(1 for f in conditions if isinstance(f, ql.User)) > 1:
			raise ql.UnsupportedFeature("queries with more than one user or uid filter are")
		conditions = [self.with_account_ids(f) if isinstance(f, ql.User) else f for f in conditions]

		boxes = [(f.south, f.west, f.north, f.east) for f in filters if isinstance(f, ql.BboxQuery)]
		if self.global_bbox is not None:
			boxes.append(self.global_bbox)
		bbox = intersect_bboxes(boxes)
		arounds = [f for f in filters if isinstance(f, ql.Around)]
		if len(arounds) > 1:
			raise ql.UnsupportedFeature("queries with more than one around filter are")
		around = None
		if len(arounds) == 1:
			around = (arounds[0].radius, self.around_positions(arounds[0]))
		if bbox is False or (around is not None and len(around[1]) == 0):
			return new_set()
		region = None if (bbox is None and around is None) else Region(bbox, around)

		id_filters = [set(f.ids) for f in filters if isinstance(f, ql.IdQuery)]
		inputs = [self.get_set(f.set) for f in filters if isinstance(f, ql.ItemFilter)]
		links = [f for f in filters if isinstance(f, ql.RecurseFilter)]

		# The tag index answers "has this key" and "has this key and value". With
		# neither, nor a set or IDs to start from, only a small area keeps the
		# search from reading the whole map.
		indexed = any(isinstance(f, HasKv) and f.mode in (HasKv.EXISTS, HasKv.EQUALS) for f in conditions)
		if not indexed and len(id_filters) == 0 and len(inputs) == 0 and len(links) == 0:
			if region is None:
				raise QueryError("a query needs a bounding box, an around filter, an ID, an input set, "
					"or a tag filter of the form [key] or [key=value]; other filters alone would "
					"search the whole map")
			if region.area() > self.limits.area_maximum:
				raise QueryError("the area is too large to search without a tag filter of the "
					"form [key] or [key=value]; the maximum is {} square degrees".format(
					self.limits.area_maximum))

		result = new_set()
		total = 0
		for kind in statement.types:
			ids = None
			candidates = id_filters + [set(inp[kind]) for inp in inputs]
			for candidate in candidates + [self.linked_ids(link, kind) for link in links]:
				ids = candidate if ids is None else ids & candidate
				if len(ids) == 0:
					break
			if ids is not None and len(ids) == 0:
				continue
			if kind == "node" or region is None:
				found = self.source.query(kind, conditions, region, ids)
			elif region.around is None and self.source.stores_bboxes():
				# One search by the stored box of each way or relation, and by
				# tags with it. A box that overlaps the area is enough, so this
				# also finds what crosses the area without a node inside it.
				found = self.source.query(kind, conditions, region, ids)
			else:
				found = self.in_region(kind, conditions, region, ids, indexed or ids is not None)
			total += len(found)
			if total > self.limits.max_elements:
				raise TooManyElements(self.limits.max_elements)
			add_to_set(result, found)
		return result

	@staticmethod
	def with_account_ids(user):
		"""A user filter by name also matches by the IDs of this server's accounts
		of those names, so that edits made before a rename are found."""
		if len(user.names) == 0:
			return user
		from django.contrib.auth.models import User
		ids = list(User.objects.filter(username__in=user.names).values_list("id", flat=True))
		return ql.User(tuple(user.uids) + tuple(ids), user.names)

	def linked_ids(self, link, kind):
		"""The IDs of the elements of one kind that a recurse filter allows."""
		source_set = self.get_set(link.set)

		def members(relations, member_kind):
			return set(ref for relation in relations for (k, ref, role) in relation.members
				if k == member_kind and (link.role is None or role == link.role))

		if link.type == "w":
			ids = set()
			if kind == "node":
				for way in source_set["way"].values():
					ids.update(way.refs)
		elif link.type == "r":
			ids = members(source_set["relation"].values(), kind)
		else:
			# Backwards: the ways or relations that have the set's elements as members
			member_kind = {"bn": "node", "bw": "way", "br": "relation"}[link.type]
			member_ids = set(source_set[member_kind])
			if kind == "way" and link.type == "bn":
				ids = set(w.id for w in self.source.ways_of_nodes(member_ids))
			elif kind == "relation":
				ids = set(r.id for r in self.source.relations_of(member_kind, member_ids)
					if any(k == member_kind and ref in member_ids and (link.role is None or role == link.role)
						for (k, ref, role) in r.members))
			else:
				ids = set()
		if len(ids) > self.intermediate_maximum():
			raise TooManyElements(self.limits.max_elements)
		return ids

	def around_positions(self, around):
		"""The (lat, lon) positions an around filter measures from."""
		if around.lat is not None:
			return ((around.lat, around.lon),)
		elements = self.get_set(around.set)
		everything = [e for kind in KINDS for e in elements[kind].values()]
		geometry = self.resolve_geometry(elements, everything)
		# Ways and relations are measured from their nodes
		positions = set()
		for element in everything:
			positions.update(output.element_points(element, geometry))
		if len(positions) > AROUND_POSITIONS_MAXIMUM:
			raise QueryError("an around filter can measure from at most {} positions; the set given "
				"has {}".format(AROUND_POSITIONS_MAXIMUM, len(positions)))
		return tuple(sorted(positions))

	# Unless the map stores a box for each way and relation, those in a region
	# are the ones with a node inside it, or for a relation a member way with
	# one, as in a map query. Distances are always worked out this way.

	def in_region(self, kind, conditions, region, ids, selective):
		small = region.area() <= self.limits.area_maximum
		if selective:
			# The other filters can be searched for by themselves. Which is
			# quicker depends on what the region holds: with few nodes in it,
			# working up from those is; otherwise searching by the filters and
			# checking what is found against the region, unless the filters
			# match a great deal of the map.
			if small and self.around_region(region, REGION_FIRST_MAXIMUM) is not None:
				return self.reached_from_region(kind, conditions, region, ids)
			try:
				found = self.source.query(kind, conditions, None, ids,
					min(self.limits.max_elements, TAG_FIRST_MAXIMUM))
			except TooManyElements:
				if not small:
					raise
			else:
				return self.having_part_in_region(kind, found, region)
		return self.reached_from_region(kind, conditions, region, ids)

	def around_region(self, region, maximum=None):
		"""The IDs of the nodes in a region, and the ways using them. With a
		maximum, None if the region holds more nodes than that."""
		key = region.key()
		if key not in self.region_cache:
			try:
				node_ids = self.source.query_ids("node", [], region, None,
					self.intermediate_maximum() if maximum is None else maximum)
				ways = self.source.ways_of_nodes(node_ids)
				self.check_size(len(ways))
			except TooManyElements:
				if maximum is None:
					raise
				return None
			self.region_cache[key] = (node_ids, ways)
		return self.region_cache[key]

	def intermediate_maximum(self):
		"""The most IDs to hold while working out a result that may itself be small."""
		return 10 * self.limits.max_elements

	def reached_from_region(self, kind, conditions, region, ids):
		"""Start from the nodes in the region and work up to the ways or relations."""
		node_ids, candidates = self.around_region(region)
		if kind == "relation":
			way_ids = set(w.id for w in candidates)
			candidates = self.source.relations_of("node", node_ids) + self.source.relations_of("way", way_ids)
			self.check_size(len(candidates))
		by_id = dict((c.id, c) for c in candidates)
		wanted = set(by_id) if ids is None else set(by_id) & ids
		if len(conditions) == 0 or len(wanted) == 0:
			return [by_id[i] for i in wanted]
		return self.source.query(kind, conditions, None, wanted)

	def having_part_in_region(self, kind, found, region):
		"""Keep the ways or relations, already found by other means, that reach into the region."""
		way_refs = {}
		if kind == "relation":
			member_ways = self.member_ids(found, "way")
			if len(member_ways) > self.intermediate_maximum():
				raise TooManyElements(self.limits.max_elements)
			for way in self.source.by_ids("way", member_ways):
				way_refs[way.id] = way.refs
			node_ids = self.member_ids(found, "node")
			for refs in way_refs.values():
				node_ids.update(refs)
		else:
			node_ids = set(ref for way in found for ref in way.refs)
		if len(node_ids) > self.intermediate_maximum():
			raise TooManyElements(self.limits.max_elements)
		inside = self.source.query_ids("node", [], region, node_ids, len(node_ids))

		def reaches(element):
			if kind == "way":
				return any(ref in inside for ref in element.refs)
			return any((member_kind == "node" and ref in inside) or
				(member_kind == "way" and any(r in inside for r in way_refs.get(ref, ())))
				for member_kind, ref, role in element.members)
		return [element for element in found if reaches(element)]

	# *** Recursion ***

	def recurse(self, kind, source_set):
		result = new_set()
		if kind in ("down", "down-rel"):
			relations = dict(source_set["relation"])
			if kind == "down-rel":
				# Follow relation members that are relations until there are no more.
				# The relations started from are part of the result.
				pending = self.member_ids(relations.values(), "relation") - set(relations)
				while pending:
					found = self.source.by_ids("relation", pending)
					add_to_set({"relation": relations}, found)
					self.check_size(len(relations))
					pending = self.member_ids(found, "relation") - set(relations)
				result["relation"] = relations

			add_to_set(result, self.source.by_ids("way", self.member_ids(relations.values(), "way")))
			node_ids = self.member_ids(relations.values(), "node")
			for way in list(source_set["way"].values()) + list(result["way"].values()):
				node_ids.update(way.refs)
			self.check_size(len(node_ids) + set_size(result))
			add_to_set(result, self.source.by_ids("node", node_ids))
			return result

		# Upwards: ways of the nodes, then relations of the nodes and of all those ways
		node_ids = set(source_set["node"])
		add_to_set(result, self.source.ways_of_nodes(node_ids))
		way_ids = set(source_set["way"]) | set(result["way"])
		add_to_set(result, self.source.relations_of("node", node_ids))
		add_to_set(result, self.source.relations_of("way", way_ids))
		if kind == "up-rel":
			# Relations holding any relation found so far, repeatedly. As in
			# Overpass, the relations started from are part of the result.
			relations = result["relation"]
			pending = set(source_set["relation"]) | set(relations)
			relations.update(source_set["relation"])
			while pending:
				found = [r for r in self.source.relations_of("relation", pending)
					if r.id not in relations]
				add_to_set(result, found)
				self.check_size(set_size(result))
				pending = set(r.id for r in found)
		return result

	@staticmethod
	def member_ids(relations, kind):
		return set(ref for relation in relations for (member_kind, ref, role) in relation.members
			if member_kind == kind)

	def check_size(self, size):
		if size > self.limits.max_elements:
			raise TooManyElements(self.limits.max_elements)

	# *** Output ***

	def print_set(self, statement):
		elements = self.get_set(statement.from_set)
		if statement.mode == "count":
			self.output.append((statement, dict((kind, len(elements[kind])) for kind in KINDS), None))
			return
		ordered = []
		for kind in KINDS:
			ordered.extend(elements[kind][i] for i in sorted(elements[kind]))
		if statement.limit is not None:
			ordered = ordered[:statement.limit]
		self.output_size += len(ordered)
		self.check_size(self.output_size)
		geometry = None
		if statement.geometry is not None:
			geometry = self.resolve_geometry(elements, ordered)
		self.output.append((statement, ordered, geometry))

	def resolve_geometry(self, elements, ordered):
		"""Positions needed to draw the elements: node positions by ID, and the
		node IDs of ways that are relation members."""
		coords = dict((n.id, (n.lat, n.lon)) for n in elements["node"].values())
		way_refs = dict((w.id, w.refs) for w in elements["way"].values())

		relations = [e for e in ordered if e.kind == "relation"]
		missing_ways = self.member_ids(relations, "way") - set(way_refs)
		self.check_size(len(missing_ways))
		for way in self.source.by_ids("way", missing_ways):
			way_refs[way.id] = way.refs

		needed = self.member_ids(relations, "node")
		for element in ordered:
			if element.kind == "way":
				needed.update(element.refs)
		for way_id in self.member_ids(relations, "way"):
			needed.update(way_refs.get(way_id, ()))
		needed -= set(coords)
		self.check_size(len(needed))
		for node in self.source.by_ids("node", needed):
			coords[node.id] = (node.lat, node.lon)
		return coords, way_refs
