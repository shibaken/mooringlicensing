# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-05-18 03:20
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0115_merge_20210517_0953'),
    ]

    operations = [
        migrations.AlterField(
            model_name='approval',
            name='expiry_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='approval',
            name='start_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='globalsettings',
            name='key',
            field=models.CharField(choices=[('dcv_permit_template_file', 'DcvPermit template file'), ('dcv_admission_template_file', 'DcvAdmission template file'), ('approval_template_file', 'Approval template file')], max_length=255),
        ),
    ]
