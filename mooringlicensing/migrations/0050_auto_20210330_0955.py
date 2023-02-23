# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-03-30 01:55
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0049_fee'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='feeseason',
            name='start_date',
        ),
        migrations.AlterField(
            model_name='feeperiod',
            name='fee_season',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='fee_seasons', to='mooringlicensing.FeeSeason'),
        ),
    ]