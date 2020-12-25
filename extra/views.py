# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse
from querymap.views import p
from rest_framework.decorators import api_view, permission_classes, parser_classes
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser
from pycrocosm import common
import pgmap
import io

# Create your views here.


