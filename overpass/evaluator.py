# -*- coding: utf-8 -*-
"""Runs a parsed Overpass query against the map database.

The state of a running query is a collection of named sets of elements, as in
Overpass. Elements are copied out of pgmap into plain Python objects as they
are read, so nothing here depends on the lifetime of a pgmap container.
"""
from __future__ import unicode_literals
import time

import pgmap
from . import ql
from .ql import QueryError, HasKv

KINDS = ("node", "way", "relation")
# Ways or relations to find by their tags before checking which are in a
# bounding box; with more than this, the search starts from the box instead.
TAG_FIRST_MAXIMUM = 20000

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
	def _arguments(tag_filters, bbox, ids):
		filters = pgmap.vectoroverpasstagfilter()
		for f in tag_filters:
			filters.append(pgmap.OverpassTagFilter(FILTER_OPS[f.mode], f.key, f.value, f.ignore_case))
		area = [] if bbox is None else [bbox[1], bbox[0], bbox[3], bbox[2]]
		return filters, area, sorted(ids or ())

	def query(self, kind, tag_filters, bbox, ids, maximum=None):
		"""Elements of one kind meeting the filters. bbox is None or (south, west,
		north, east), and only finds nodes. ids is None or the IDs to look among.
		Finding more than maximum, by default the most a query may select, is an error."""
		if ids is not None and len(ids) == 0:
			return []
		if maximum is None:
			maximum = self.limits.max_elements
		filters, area, id_list = self._arguments(tag_filters, bbox, ids)
		found = self._run(lambda data: self.transaction.OverpassQuery(
			kind, filters, area, id_list, maximum + 1, data))
		if len(found) > maximum:
			raise TooManyElements(self.limits.max_elements)
		return found

	def query_ids(self, kind, tag_filters, bbox, ids, maximum):
		"""As query, but only the IDs of what is found, as a set."""
		if ids is not None and len(ids) == 0:
			return set()
		filters, area, id_list = self._arguments(tag_filters, bbox, ids)
		found = pgmap.vectori64()
		self._guarded(lambda: self.transaction.OverpassQueryIds(
			kind, filters, area, id_list, maximum + 1, found))
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

class Evaluator(object):
	def __init__(self, source, limits):
		self.source = source
		self.limits = limits
		self.sets = {}
		self.global_bbox = None
		self.output = [] # (Print statement, elements in output order, or counts by kind)
		self.output_size = 0
		self.bbox_cache = {}

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
		tag_filters = [f for f in statement.filters if isinstance(f, HasKv)]
		boxes = [(f.south, f.west, f.north, f.east) for f in statement.filters
			if isinstance(f, ql.BboxQuery)]
		if self.global_bbox is not None:
			boxes.append(self.global_bbox)
		bbox = intersect_bboxes(boxes)

		id_filters = [set(f.ids) for f in statement.filters if isinstance(f, ql.IdQuery)]
		inputs = [self.get_set(f.set) for f in statement.filters if isinstance(f, ql.ItemFilter)]

		# The tag index answers "has this key" and "has this key and value". With
		# neither, nor a set or IDs to start from, only a small area keeps the
		# search from reading the whole map.
		indexed = any(f.mode in (HasKv.EXISTS, HasKv.EQUALS) for f in tag_filters)
		if not indexed and len(id_filters) == 0 and len(inputs) == 0:
			if bbox is None:
				raise QueryError("a query needs a bounding box, an ID, an input set, or a tag filter "
					"of the form [key] or [key=value]; other tag filters alone would search the whole map")
			if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > self.limits.area_maximum:
				raise QueryError("the bounding box is too large to search without a tag filter of the "
					"form [key] or [key=value]; the maximum is {} square degrees".format(
					self.limits.area_maximum))

		result = new_set()
		if bbox is False:
			return result
		total = 0
		for kind in statement.types:
			ids = None
			for candidate in id_filters + [set(inp[kind]) for inp in inputs]:
				ids = candidate if ids is None else ids & candidate
			if ids is not None and len(ids) == 0:
				continue
			if kind == "node" or bbox is None:
				found = self.source.query(kind, tag_filters, bbox, ids)
			else:
				found = self.in_bbox(kind, tag_filters, bbox, ids, indexed or ids is not None)
			total += len(found)
			if total > self.limits.max_elements:
				raise TooManyElements(self.limits.max_elements)
			add_to_set(result, found)
		return result

	# Ways and relations in a bounding box are those with a node inside it, or
	# for a relation a member way with one, as in a map query. The map does not
	# reliably store a box for each way to search by instead.

	def in_bbox(self, kind, tag_filters, bbox, ids, selective):
		small = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) <= self.limits.area_maximum
		if selective:
			# The other filters can be searched for by themselves, which is far
			# quicker than working up from every node in the box, unless they
			# match a great deal of the map.
			try:
				found = self.source.query(kind, tag_filters, None, ids,
					min(self.limits.max_elements, TAG_FIRST_MAXIMUM))
			except TooManyElements:
				if not small:
					raise
			else:
				return self.having_part_in_bbox(kind, found, bbox)
		return self.reached_from_bbox(kind, tag_filters, bbox, ids)

	def around_bbox(self, bbox):
		"""The IDs of the nodes in a box, and the ways using them."""
		if bbox not in self.bbox_cache:
			node_ids = self.source.query_ids("node", [], bbox, None, self.intermediate_maximum())
			ways = self.source.ways_of_nodes(node_ids)
			self.check_size(len(ways))
			self.bbox_cache[bbox] = (node_ids, ways)
		return self.bbox_cache[bbox]

	def intermediate_maximum(self):
		"""The most IDs to hold while working out a result that may itself be small."""
		return 10 * self.limits.max_elements

	def reached_from_bbox(self, kind, tag_filters, bbox, ids):
		"""Start from the nodes in the box and work up to the ways or relations."""
		node_ids, candidates = self.around_bbox(bbox)
		if kind == "relation":
			way_ids = set(w.id for w in candidates)
			candidates = self.source.relations_of("node", node_ids) + self.source.relations_of("way", way_ids)
			self.check_size(len(candidates))
		by_id = dict((c.id, c) for c in candidates)
		wanted = set(by_id) if ids is None else set(by_id) & ids
		if len(tag_filters) == 0 or len(wanted) == 0:
			return [by_id[i] for i in wanted]
		return self.source.query(kind, tag_filters, None, wanted)

	def having_part_in_bbox(self, kind, found, bbox):
		"""Keep the ways or relations, already found by other means, that reach into the box."""
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
		inside = self.source.query_ids("node", [], bbox, node_ids, len(node_ids))

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
