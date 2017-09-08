# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models

# Create your models here.

class Nodes(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    changeset = models.BigIntegerField(blank=True, null=True)
    username = models.TextField(blank=True, null=True)
    uid = models.IntegerField(blank=True, null=True)
    visible = models.NullBooleanField()
    timestamp = models.BigIntegerField(blank=True, null=True)
    version = models.IntegerField(blank=True, null=True)
    current = models.NullBooleanField()
    tags = models.TextField(blank=True, null=True)  # This field type is a guess.
    geom = models.TextField(blank=True, null=True)  # This field type is a guess.

    class Meta:
        managed = False
        db_table = 'portsmouth_nodes'

class Ways(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    changeset = models.BigIntegerField(blank=True, null=True)
    username = models.TextField(blank=True, null=True)
    uid = models.IntegerField(blank=True, null=True)
    visible = models.NullBooleanField()
    timestamp = models.BigIntegerField(blank=True, null=True)
    version = models.IntegerField(blank=True, null=True)
    current = models.NullBooleanField()
    tags = models.TextField(blank=True, null=True)  # This field type is a guess.
    members = models.TextField(blank=True, null=True)  # This field type is a guess.

    class Meta:
        managed = False
        db_table = 'portsmouth_ways'

class Relations(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    changeset = models.BigIntegerField(blank=True, null=True)
    username = models.TextField(blank=True, null=True)
    uid = models.IntegerField(blank=True, null=True)
    visible = models.NullBooleanField()
    timestamp = models.BigIntegerField(blank=True, null=True)
    version = models.IntegerField(blank=True, null=True)
    current = models.NullBooleanField()
    tags = models.TextField(blank=True, null=True)  # This field type is a guess.
    members = models.TextField(blank=True, null=True)  # This field type is a guess.
    memberroles = models.TextField(blank=True, null=True)  # This field type is a guess.

    class Meta:
        managed = False
        db_table = 'portsmouth_relations'

class WayMems(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    version = models.IntegerField(blank=True, null=True)
    member = models.BigIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'portsmouth_way_mems'

class RelationMemsN(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    version = models.IntegerField(blank=True, null=True)
    member = models.BigIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'portsmouth_relation_mems_n'

class RelationMemsW(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    version = models.IntegerField(blank=True, null=True)
    member = models.BigIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'portsmouth_relation_mems_w'

class RelationMemsR(models.Model):
    id = models.BigIntegerField(blank=True, null=False, primary_key=True)
    version = models.IntegerField(blank=True, null=True)
    member = models.BigIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'portsmouth_relation_mems_r'

