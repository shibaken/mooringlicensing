# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-10-06 01:56
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0263_auto_20211006_0955'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='applicationfee',
            name='fee_items_additional_aa',
        ),
    ]
