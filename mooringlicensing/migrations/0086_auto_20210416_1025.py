# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-04-16 02:25
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0085_auto_20210415_1632'),
    ]

    operations = [
        migrations.AddField(
            model_name='dcvpermit',
            name='dcv_organisation',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='mooringlicensing.DcvOrganisation'),
        ),
        migrations.AddField(
            model_name='dcvpermit',
            name='dcv_vessel',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='mooringlicensing.DcvVessel'),
        ),
    ]
