# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-04-23 03:25
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0099_auto_20210423_1121'),
    ]

    operations = [
        migrations.RenameField(
            model_name='admissiontype',
            old_name='type',
            new_name='code',
        ),
        migrations.RenameField(
            model_name='agegroup',
            old_name='name',
            new_name='code',
        ),
    ]
