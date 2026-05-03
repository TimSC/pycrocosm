# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from rest_framework.parsers import BaseParser
from rest_framework.exceptions import ParseError
from defusedxml.ElementTree import parse
from django.conf import settings
import io
import pgmap

def get_xml_upload_maximum_bytes():
	return getattr(settings, 'XML_UPLOAD_MAXIMUM_BYTES', 10 * 1024 * 1024)

def limit_message(settingName, value, actual=None):
	if actual is None:
		return "{} limit exceeded; maximum is {}".format(settingName, value)
	return "{} limit exceeded; maximum is {}, got {}".format(settingName, value, actual)

def raise_decoder_parse_error(dec, detail):
	dec.output = None
	try:
		if not dec.parseCompletedOk:
			dec.DecodeFinish()
	except RuntimeError:
		pass
	raise ParseError(detail=detail)

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
		return parse(read_limited(stream))

class OsmDataXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmData()
		dec = pgmap.OsmXmlDecodeString()
		dec.output = data
		pageSize = 100000
		totalBytes = 0
		maxBytes = get_xml_upload_maximum_bytes()
		while True:
			inputXml = stream.read(pageSize)
			if len(inputXml) == 0:
				break
			totalBytes += len(inputXml)
			if totalBytes > maxBytes:
				raise_decoder_parse_error(dec, limit_message("XML_UPLOAD_MAXIMUM_BYTES", maxBytes, totalBytes))
			if not dec.DecodeSubString(inputXml.decode("UTF-8"), len(inputXml), False) and len(dec.errString) > 0:
				raise_decoder_parse_error(dec, dec.errString)
		dec.DecodeSubString("", 0, True)
		if not dec.parseCompletedOk and len(dec.errString) > 0:
			raise_decoder_parse_error(dec, dec.errString)
		dec.DecodeFinish()
		dec.output = None
		if not dec.parseCompletedOk:
			raise_decoder_parse_error(dec, dec.errString)
		del dec
		return data

class OsmChangeXmlParser(BaseParser):
	media_type = 'text/xml'
	def parse(self, stream, media_type, parser_context):
		data = pgmap.OsmChange()
		dec = pgmap.OsmChangeXmlDecodeString()
		dec.output = data
		pageSize = 100000
		totalBytes = 0
		maxBytes = get_xml_upload_maximum_bytes()
		while True:
			inputXml = stream.read(pageSize)
			if len(inputXml) == 0:
				break
			totalBytes += len(inputXml)
			if totalBytes > maxBytes:
				raise_decoder_parse_error(dec, limit_message("XML_UPLOAD_MAXIMUM_BYTES", maxBytes, totalBytes))
			if not dec.DecodeSubString(inputXml.decode("UTF-8"), len(inputXml), False) and len(dec.errString) > 0:
				raise_decoder_parse_error(dec, dec.errString)
		dec.DecodeSubString("", 0, True)
		if not dec.parseCompletedOk and len(dec.errString) > 0:
			raise_decoder_parse_error(dec, dec.errString)
		dec.DecodeFinish()
		dec.output = None
		if not dec.parseCompletedOk:
			raise_decoder_parse_error(dec, dec.errString)
		del dec
		return data
