# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-08-05 04:03
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0202_auto_20210805_1149'),
    ]

    operations = [
        migrations.AddField(
            model_name='stickerprintingresponse',
            name='email_date',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
