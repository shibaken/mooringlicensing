# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-06-21 02:55
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0161_auto_20210621_1000'),
    ]

    operations = [
        migrations.AddField(
            model_name='numberofdaystype',
            name='code',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
