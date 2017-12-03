# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from django import template
import datetime

register = template.Library()

@register.filter(name='todatetime')
def todatetime(value):
    return datetime.datetime.fromtimestamp(value)

