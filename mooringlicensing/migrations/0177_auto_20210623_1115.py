# -*- coding: utf-8 -*-
# Generated by Django 1.11.29 on 2021-06-23 03:15
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
import mooringlicensing.components.approvals.models


class Migration(migrations.Migration):

    dependencies = [
        ('mooringlicensing', '0176_auto_20210622_1634'),
    ]

    operations = [
        migrations.CreateModel(
            name='RenewalDocument',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=255, verbose_name='name')),
                ('description', models.TextField(blank=True, verbose_name='description')),
                ('uploaded_date', models.DateTimeField(auto_now_add=True)),
                ('_file', models.FileField(upload_to=mooringlicensing.components.approvals.models.update_approval_doc_filename)),
                ('can_delete', models.BooleanField(default=True)),
            ],
        ),
        migrations.AlterField(
            model_name='approval',
            name='renewal_document',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='renewal_document', to='mooringlicensing.RenewalDocument'),
        ),
        migrations.AddField(
            model_name='renewaldocument',
            name='approval',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='renewal_documents', to='mooringlicensing.Approval'),
        ),
    ]