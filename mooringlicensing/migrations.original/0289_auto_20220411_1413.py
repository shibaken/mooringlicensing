# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2022-04-11 06:13
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0288_auto_20220408_1512'),
    ]

    operations = [
        migrations.AlterField(
            model_name='compliance',
            name='customer_status',
            field=models.CharField(choices=[('due', 'Due'), ('overdue', 'Overdue'), ('future', 'Future'), ('with_assessor', 'Under Review'), ('approved', 'Approved'), ('discarded', 'Discarded')], max_length=20),
        ),
    ]