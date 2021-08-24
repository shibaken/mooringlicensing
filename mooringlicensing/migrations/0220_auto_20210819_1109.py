# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-08-19 03:09
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0219_remove_feeitem_incremental_amount'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='vesselsizecategory',
            name='incremental',
        ),
        migrations.AddField(
            model_name='feeitem',
            name='incremental_amount',
            field=models.BooleanField(default=False),
        ),
    ]
