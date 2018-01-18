# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from rest_framework.parsers import BaseParser
from rest_framework.exceptions import ParseError
from defusedxml.ElementTree import parse
import pgmap

class DefusedXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		return parse(stream)

class OsmDataXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmData()
		dec = pgmap.OsmXmlDecodeString()
		dec.output = data
		pageSize = 100000
		while True:
			inputXml = stream.read(pageSize)
			if len(inputXml) == 0:
				break
			dec.DecodeSubString(inputXml.decode("UTF-8"), len(inputXml), False)
		dec.DecodeSubString("", 0, True)
		if not dec.parseCompletedOk:
			raise ParseError(detail=dec.errString)
		return data

class OsmChangeXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmChange()
		dec = pgmap.OsmChangeXmlDecodeString()
		dec.output = data
		pageSize = 100000
		while True:
			inputXml = stream.read(pageSize)
			if len(inputXml) == 0:
				break
			dec.DecodeSubString(inputXml.decode("UTF-8"), len(inputXml), False)
		dec.DecodeSubString("", 0, True)
		if not dec.parseCompletedOk:
			raise ParseError(detail=dec.errString)
		return data

