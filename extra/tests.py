# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from changeset.tests import CreateTestChangeset, GetObj, ParseOsmDiffToDict
from querymap.tests import DecodeOsmdataResponse
from changeset.views import GetOsmDataIndex
from defusedxml.ElementTree import parse, fromstring
from querymap.views import p
import pgmap
import sys
import gc

# Create your tests here.


