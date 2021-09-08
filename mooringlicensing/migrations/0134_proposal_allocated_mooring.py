# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-06-02 07:08
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0133_auto_20210602_1429'),
    ]

    operations = [
        migrations.AddField(
            model_name='proposal',
            name='allocated_mooring',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ria_generated_proposal', to='mooringlicensing.Mooring'),
        ),
    ]
