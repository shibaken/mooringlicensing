# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-04-08 08:23
from __future__ import unicode_literals

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0064_merge_20210408_0902'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposal',
            name='bay_preferences_numbered',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.IntegerField(blank=True, null=True), blank=True, null=True, size=None),
        ),
    ]