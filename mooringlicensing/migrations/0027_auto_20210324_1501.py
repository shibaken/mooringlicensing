# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-03-24 07:01
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0026_auto_20210324_1500'),
    ]

    operations = [
        migrations.AlterField(
            model_name='owner',
            name='org_name',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
