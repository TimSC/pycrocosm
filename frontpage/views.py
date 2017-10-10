# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render, reverse

# Create your views here.

def index(request):
	return render(request, 'frontpage/index.html', {'apibase': request.build_absolute_uri(reverse("apibase"))})

