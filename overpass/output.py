"""Writes query results in the Overpass API's JSON and XML layouts.

These are not the layouts of the OSM editing API: there is a different header,
ways and relations can carry their geometry, and a count is an element.
"""
import datetime
import json
import re
from xml.sax.saxutils import quoteattr, escape

KINDS = ("node", "way", "relation")
# Characters that XML 1.0 cannot hold, even escaped
XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

def iso_time(timestamp):
	return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class Shape:
	"""What an out statement shows of each element."""
	def __init__(self, statement):
		mode = statement.mode
		self.geometry = statement.geometry
		self.members = mode in ("skel", "body", "meta")
		self.coords = self.members or self.geometry is not None
		self.tags = mode in ("body", "tags", "meta")
		self.meta = mode == "meta"

def meta_fields(element):
	"""The metadata of an element that is known, in output order."""
	out = []
	if element.timestamp:
		out.append(("timestamp", iso_time(element.timestamp)))
	if element.version:
		out.append(("version", element.version))
	if element.changeset:
		out.append(("changeset", element.changeset))
	if element.user:
		out.append(("user", element.user))
	if element.uid:
		out.append(("uid", element.uid))
	return out

def way_points(refs, coords):
	"""Positions of a way's nodes as (lat, lon), or None where one is not known."""
	return [coords.get(ref) for ref in refs]

def element_points(element, geometry):
	"""Every known position that is part of an element."""
	coords, way_refs = geometry
	if element.kind == "node":
		return [(element.lat, element.lon)]
	if element.kind == "way":
		return [p for p in way_points(element.refs, coords) if p is not None]
	points = []
	for kind, ref, role in element.members:
		if kind == "node" and ref in coords:
			points.append(coords[ref])
		elif kind == "way":
			points.extend(p for p in way_points(way_refs.get(ref, ()), coords) if p is not None)
	return points

def bounds_of(points):
	"""(minlat, minlon, maxlat, maxlon), or None without points."""
	if len(points) == 0:
		return None
	lats = [p[0] for p in points]
	lons = [p[1] for p in points]
	return (min(lats), min(lons), max(lats), max(lons))

def center_of(bounds):
	return ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)

# ****** JSON ******

def json_point(point):
	return None if point is None else {"lat": point[0], "lon": point[1]}

def json_element(element, shape, geometry):
	out = {"type": element.kind, "id": element.id}
	if element.kind == "node" and shape.coords:
		out["lat"], out["lon"] = element.lat, element.lon
	if shape.meta:
		out.update(meta_fields(element))

	if element.kind != "node" and shape.geometry is not None:
		bounds = bounds_of(element_points(element, geometry))
		if bounds is not None and shape.geometry == "center":
			out["center"] = json_point(center_of(bounds))
		elif bounds is not None:
			out["bounds"] = {"minlat": bounds[0], "minlon": bounds[1], "maxlat": bounds[2], "maxlon": bounds[3]}

	with_geometry = shape.geometry == "geom"
	if element.kind == "way":
		if shape.members:
			out["nodes"] = element.refs
		if with_geometry:
			out["geometry"] = [json_point(p) for p in way_points(element.refs, geometry[0])]
	elif element.kind == "relation" and (shape.members or with_geometry):
		members = []
		for kind, ref, role in element.members:
			member = {"type": kind, "ref": ref, "role": role}
			if with_geometry:
				coords, way_refs = geometry
				if kind == "node" and ref in coords:
					member["lat"], member["lon"] = coords[ref]
				elif kind == "way" and ref in way_refs:
					member["geometry"] = [json_point(p) for p in way_points(way_refs[ref], coords)]
			members.append(member)
		out["members"] = members

	if shape.tags and len(element.tags) > 0:
		out["tags"] = element.tags
	return out

def count_tags(counts):
	"""The tags of a count element, in output order."""
	return [("nodes", str(counts["node"])), ("ways", str(counts["way"])),
		("relations", str(counts["relation"])), ("total", str(sum(counts.values())))]

def write_json(output, generator, copyright, now):
	elements = []
	for statement, content, geometry in output:
		if statement.mode == "count":
			elements.append({"type": "count", "id": 0, "tags": dict(count_tags(content))})
			continue
		shape = Shape(statement)
		elements.extend(json_element(element, shape, geometry) for element in content)
	osm3s = {"timestamp_osm_base": iso_time(now)}
	if copyright:
		osm3s["copyright"] = copyright
	doc = {"version": 0.6, "generator": generator, "osm3s": osm3s, "elements": elements}
	return json.dumps(doc, ensure_ascii=False).encode("utf-8")

# ****** XML ******

def attr(value):
	return quoteattr(XML_ILLEGAL.sub("", "{}".format(value)))

def coord(value):
	return "{:.7f}".format(value)

def xml_element(element, shape, geometry, out):
	attribs = [("id", element.id)]
	if element.kind == "node" and shape.coords:
		attribs += [("lat", coord(element.lat)), ("lon", coord(element.lon))]
	if shape.meta:
		attribs += meta_fields(element)
	out.append("  <{}{}".format(element.kind, "".join(" {}={}".format(k, attr(v)) for k, v in attribs)))

	children = []
	if element.kind != "node" and shape.geometry is not None:
		bounds = bounds_of(element_points(element, geometry))
		if bounds is not None and shape.geometry == "center":
			center = center_of(bounds)
			children.append('    <center lat="{}" lon="{}"/>'.format(coord(center[0]), coord(center[1])))
		elif bounds is not None:
			children.append('    <bounds minlat="{}" minlon="{}" maxlat="{}" maxlon="{}"/>'.format(
				*[coord(v) for v in bounds]))

	def position(point):
		return "" if point is None else ' lat="{}" lon="{}"'.format(coord(point[0]), coord(point[1]))

	with_geometry = shape.geometry == "geom"
	if element.kind == "way" and (shape.members or with_geometry):
		points = way_points(element.refs, geometry[0]) if with_geometry else [None] * len(element.refs)
		for ref, point in zip(element.refs, points):
			children.append('    <nd ref="{}"{}/>'.format(ref, position(point)))
	elif element.kind == "relation" and (shape.members or with_geometry):
		for kind, ref, role in element.members:
			start = '    <member type="{}" ref="{}" role={}'.format(kind, ref, attr(role))
			coords, way_refs = geometry if with_geometry else ({}, {})
			if kind == "node" and ref in coords:
				children.append(start + position(coords[ref]) + "/>")
			elif kind == "way" and ref in way_refs:
				children.append(start + ">")
				for point in way_points(way_refs[ref], coords):
					children.append("      <nd{}/>".format(position(point)))
				children.append("    </member>")
			else:
				children.append(start + "/>")

	if shape.tags:
		for key in sorted(element.tags):
			children.append("    <tag k={} v={}/>".format(attr(key), attr(element.tags[key])))

	if len(children) == 0:
		out[-1] += "/>"
	else:
		out[-1] += ">"
		out.extend(children)
		out.append("  </{}>".format(element.kind))

def write_xml(output, generator, copyright, now, root_attribs=None):
	"""root_attribs, if given, are extra attributes of the root element, and
	the Overpass note and meta elements are left out: this is the layout of
	the OSM API, which the XAPI calls use."""
	out = ['<?xml version="1.0" encoding="UTF-8"?>']
	root = [("version", "0.6"), ("generator", generator)]
	if root_attribs is not None:
		root += [(k, v) for k, v in root_attribs if v]
	out.append("<osm{}>".format("".join(" {}={}".format(k, attr(v)) for k, v in root)))
	if root_attribs is None:
		if copyright:
			out.append("<note>{}</note>".format(escape(XML_ILLEGAL.sub("", copyright))))
		out.append('<meta osm_base="{}"/>'.format(iso_time(now)))
		out.append("")

	for statement, content, geometry in output:
		if statement.mode == "count":
			out.append('  <count id="0">')
			for key, value in count_tags(content):
				out.append('    <tag k="{}" v="{}"/>'.format(key, value))
			out.append("  </count>")
			continue
		shape = Shape(statement)
		for element in content:
			xml_element(element, shape, geometry, out)
	out.append("")
	out.append("</osm>")
	return "\n".join(out).encode("utf-8")

# ****** CSV ******

OTYPES = {"node": 1, "way": 2, "relation": 3}
META_FIELDS = ("version", "timestamp", "changeset", "uid", "user")

def csv_value(field, element, shape, geometry):
	if not field.startswith("::"):
		return element.tags.get(field, "") if shape.tags else ""
	name = field[2:]
	if name == "id":
		return str(element.id)
	if name == "type":
		return element.kind
	if name == "otype":
		return str(OTYPES[element.kind])
	if name in ("lat", "lon"):
		# Known for nodes, and for anything printed with its center
		point = None
		if element.kind == "node" and shape.coords:
			point = (element.lat, element.lon)
		elif element.kind != "node" and shape.geometry == "center":
			bounds = bounds_of(element_points(element, geometry))
			point = None if bounds is None else center_of(bounds)
		if point is None:
			return ""
		return coord(point[0] if name == "lat" else point[1])
	if name in META_FIELDS and shape.meta:
		return "{}".format(dict(meta_fields(element)).get(name, ""))
	return ""

def write_csv(output, csv_format):
	"""Tabular output: a row for each element, and for each count. Values are
	written as they are, without quoting, as Overpass writes them."""
	lines = []
	if csv_format.header:
		lines.append(csv_format.separator.join(
			"@" + f[2:] if f.startswith("::") else f for f in csv_format.columns))
	for statement, content, geometry in output:
		if statement.mode == "count":
			counts = {"::type": "count", "::id": "0", "::count": str(sum(content.values())),
				"::count:nodes": str(content["node"]), "::count:ways": str(content["way"]),
				"::count:relations": str(content["relation"]), "::count:areas": "0"}
			lines.append(csv_format.separator.join(counts.get(f, "") for f in csv_format.columns))
			continue
		shape = Shape(statement)
		for element in content:
			lines.append(csv_format.separator.join(
				csv_value(f, element, shape, geometry) for f in csv_format.columns))
	return ("\n".join(lines) + "\n").encode("utf-8")
