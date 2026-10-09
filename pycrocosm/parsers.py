from rest_framework.parsers import BaseParser
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from defusedxml.ElementTree import parse
from django.conf import settings
import io
import zlib
import pgmap
from pycrocosm.mapdb import make_xml_limits

def get_xml_upload_maximum_bytes():
	return getattr(settings, 'XML_UPLOAD_MAXIMUM_BYTES', 10 * 1024 * 1024)

def limit_message(settingName, value, actual=None):
	if actual is None:
		return "{} limit exceeded; maximum is {}".format(settingName, value)
	return "{} limit exceeded; maximum is {}, got {}".format(settingName, value, actual)

# cppo5m names each limit after its own field; uploads are told which setting it was
LIMIT_SETTING_NAMES = {
	"maxBytes": "XML_UPLOAD_MAXIMUM_BYTES",
	"maxDepth": "PGMAP_XML_MAX_DEPTH",
	"maxObjects": "CHANGESETS_MAXIMUM_ELEMENTS",
	"maxTagsPerObject": "PGMAP_XML_MAX_TAGS_PER_OBJECT",
	"maxWayNodesPerObject": "WAYNODES_MAXIMUM",
	"maxRelationMembersPerObject": "RELATION_MEMBERS_MAXIMUM",
	"maxAttributesPerElement": "PGMAP_XML_MAX_ATTRIBUTES_PER_ELEMENT",
	"maxAttributeBytes": "PGMAP_XML_MAX_ATTRIBUTE_BYTES",
}

def decode_error_detail(err):
	"""Describe a pgmap decoding failure for the client."""
	if isinstance(err, pgmap.OsmLimitError) and len(err.args) == 4:
		_, limit, maximum, actual = err.args
		return limit_message(LIMIT_SETTING_NAMES.get(limit, limit), maximum, actual)
	return str(err)

class DecompressedStream:
	"""Reads the decompressed form of a compressed request body.

	Nothing is decompressed beyond what each read asks for, so the upload
	limits apply to the decompressed size without the body being expanded first.
	"""
	def __init__(self, stream, wbits):
		self.stream = stream
		self.decompressor = zlib.decompressobj(wbits)
		self.finished = False

	def read(self, size):
		try:
			while not self.finished:
				compressed = self.decompressor.unconsumed_tail
				atEnd = False
				if len(compressed) == 0:
					compressed = self.stream.read(16384)
					atEnd = len(compressed) == 0
				# With no input left this still returns output held back by
				# the size of an earlier read
				out = self.decompressor.decompress(compressed, size)
				if atEnd and len(out) == 0 and not self.decompressor.eof:
					raise ParseError(detail="Compressed request body ends part way through")
				if self.decompressor.eof:
					# Anything after the end of the compressed data is ignored
					self.finished = True
				if len(out) > 0:
					return out
		except zlib.error as err:
			raise ParseError(detail="Request body could not be decompressed: {}".format(err))
		return b""

def body_stream(stream, parser_context):
	"""The request body as the client wrote it, undoing any Content-Encoding."""
	request = (parser_context or {}).get('request')
	encoding = request.META.get('HTTP_CONTENT_ENCODING', '') if request is not None else ''
	encoding = encoding.strip().lower()
	if encoding in ('', 'identity'):
		return stream
	if encoding in ('gzip', 'x-gzip'):
		return DecompressedStream(stream, 16 + zlib.MAX_WBITS)
	if encoding == 'deflate':
		return DecompressedStream(stream, zlib.MAX_WBITS)
	raise UnsupportedMediaType(encoding, detail="Content-Encoding {} is not supported".format(encoding))

def feed_upload(stream, parser):
	"""Pass an uploaded document to a pgmap push parser in pieces.

	The parser enforces the upload limits, including the total size, so the
	body is never held in memory whole.
	"""
	pageSize = 100000
	try:
		while True:
			inputXml = stream.read(pageSize)
			if len(inputXml) == 0:
				break
			parser.FeedBytes(inputXml, False)
		parser.FeedBytes(b"", True)
	except pgmap.OsmDecodeError as err:
		raise ParseError(detail=decode_error_detail(err))

def read_limited(stream):
	maxBytes = get_xml_upload_maximum_bytes()
	pageSize = 100000
	totalBytes = 0
	out = io.BytesIO()
	while True:
		inputXml = stream.read(pageSize)
		if len(inputXml) == 0:
			break
		totalBytes += len(inputXml)
		if totalBytes > maxBytes:
			raise ParseError(detail=limit_message("XML_UPLOAD_MAXIMUM_BYTES", maxBytes, totalBytes))
		out.write(inputXml)
	out.seek(0)
	return out

class DefusedXmlParser(BaseParser):
	media_type = '*/*'
	def parse(self, stream, media_type, parser_context):
		return parse(read_limited(body_stream(stream, parser_context)))

class OsmDataXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmData()
		feed_upload(body_stream(stream, parser_context), pgmap.OsmXmlParser(data, make_xml_limits()))
		return data

class OsmChangeXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmChange()
		feed_upload(body_stream(stream, parser_context), pgmap.OsmChangeXmlParser(data, make_xml_limits()))
		return data
