# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-06-18 08:09
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0159_merge_20210617_1552'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='numberofdayssetting',
            options={'ordering': ['-date_of_enforcement']},
        ),
        migrations.AlterModelOptions(
            name='numberofdaystype',
            options={'verbose_name': 'Number of days Settings', 'verbose_name_plural': 'Number of days Settings'},
        ),
    ]
