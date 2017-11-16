from django import template
import datetime

register = template.Library()

@register.filter(name='todatetime')
def todatetime(value):
    return datetime.datetime.fromtimestamp(value)

