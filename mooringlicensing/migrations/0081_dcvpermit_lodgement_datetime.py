# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-04-15 03:49
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0080_remove_dcvpermit_lodgement_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='dcvpermit',
            name='lodgement_datetime',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
