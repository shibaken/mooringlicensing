# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-05-25 23:44
from __future__ import unicode_literals

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0126_merge_20210525_1438'),
    ]

    operations = [
        migrations.AddField(
            model_name='mooringlicenceapplication',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False),
        ),
    ]
