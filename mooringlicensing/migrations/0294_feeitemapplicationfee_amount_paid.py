# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2022-05-18 05:51
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0293_proposal_null_vessel_on_create'),
    ]

    operations = [
        migrations.AddField(
            model_name='feeitemapplicationfee',
            name='amount_paid',
            field=models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=8, null=True),
        ),
    ]