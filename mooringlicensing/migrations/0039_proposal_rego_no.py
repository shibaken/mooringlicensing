# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-03-30 07:43
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0038_auto_20210326_1723'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposal',
            name='rego_no',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
