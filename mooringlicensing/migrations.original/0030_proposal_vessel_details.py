# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-03-25 01:52
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0029_auto_20210325_0946'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposal',
            name='vessel_details',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='mooringlicensing.VesselDetails'),
        ),
    ]