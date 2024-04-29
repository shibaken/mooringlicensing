from __future__ import unicode_literals
import math
from django.core.files.storage import FileSystemStorage

import os

from django_countries.fields import CountryField
from dateutil.relativedelta import relativedelta

import json
import datetime
from decimal import Decimal
import traceback
import django_countries

import pytz
import uuid
from mooringlicensing.components.approvals.email import send_aup_revoked_due_to_mooring_swap_email

from mooringlicensing.ledger_api_utils import retrieve_email_userro, get_invoice_payment_status
from ledger_api_client.utils import calculate_excl_gst
# from mooringlicensing.components.payments_ml.utils import get_invoice_payment_status
# from mooringlicensing.components.main.utils import retrieve_email_user
# from ledger.settings_base import TIME_ZONE
from mooringlicensing.settings import PROPOSAL_TYPE_SWAP_MOORINGS, TIME_ZONE, GROUP_ASSESSOR_MOORING_LICENCE, \
    GROUP_APPROVER_MOORING_LICENCE, PRIVATE_MEDIA_STORAGE_LOCATION, PRIVATE_MEDIA_BASE_URL
# from ledger.payments.pdf import create_invoice_pdf_bytes
# from ledger_api_client.pdf import create_invoice_pdf_bytes
from django.db import models, transaction
from django.dispatch import receiver
from django.db.models.signals import pre_delete
# from django.utils.encoding import python_2_unicode_compatible
from django.core.exceptions import ValidationError, ObjectDoesNotExist, ImproperlyConfigured
from django.contrib.postgres.fields.jsonb import JSONField
from django.contrib.postgres.fields import ArrayField
from django.contrib.auth.models import Group
from django.utils import timezone
# from django.conf import settings
# from django.core.urlresolvers import reverse
from django.urls import reverse
# from ledger.accounts.models import EmailUser, RevisionedMixin
from ledger_api_client.ledger_models import EmailUserRO as EmailUser, EmailUserRO, BaseAddress
# from ledger.payments.invoice.models import Invoice
from ledger_api_client.ledger_models import Invoice
from mooringlicensing import exceptions, settings
from mooringlicensing.components.organisations.models import Organisation
from mooringlicensing.components.main.models import (
    CommunicationsLogEntry,
    GlobalSettings,
    UserAction,
    Document, ApplicationType, NumberOfDaysType, NumberOfDaysSetting, RevisionedMixin,
)
import requests
import ledger_api_client
from mooringlicensing.components.main.decorators import (
        basic_exception_handler, 
        timeit, 
        query_debugger
        )
# from ledger.checkout.utils import createCustomBasket
# from ledger.payments.invoice.utils import CreateInvoiceBasket

from mooringlicensing.components.proposals.email import (
    send_application_approved_or_declined_email,
    send_amendment_email_notification,
    send_confirmation_email_upon_submit,
    send_approver_approve_decline_email_notification,
    send_proposal_approver_sendback_email_notification, send_endorsement_of_authorised_user_application_email,
    send_documents_upload_for_mooring_licence_application_email,
    send_other_documents_submitted_notification_email, send_notification_email_upon_submit_to_assessor,
    send_au_summary_to_ml_holder, send_application_discarded_email,
)
from mooringlicensing.ordered_model import OrderedModel
import copy
import subprocess
from django.db.models import Q, Max
from reversion.models import Version
from dirtyfields import DirtyFieldsMixin
from rest_framework import serializers

import logging

from mooringlicensing.settings import PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL, PAYMENT_SYSTEM_ID, \
    LEDGER_SYSTEM_ID, PROPOSAL_TYPE_NEW, CODE_DAYS_FOR_ENDORSER_AUA

logger = logging.getLogger(__name__)
# logger_for_payment = logging.getLogger('mooringlicensing')
logger_for_payment = logging.getLogger(__name__)

private_storage = FileSystemStorage(  # We want to store files in secure place (outside of the media folder)
    location=PRIVATE_MEDIA_STORAGE_LOCATION,
    base_url=PRIVATE_MEDIA_BASE_URL,
)

def update_proposal_doc_filename(instance, filename):
    return '{}/proposals/{}/documents/{}'.format(settings.MEDIA_APP_DIR, instance.proposal.id,filename)

def update_onhold_doc_filename(instance, filename):
    return '{}/proposals/{}/on_hold/{}'.format(settings.MEDIA_APP_DIR, instance.proposal.id,filename)

def update_proposal_required_doc_filename(instance, filename):
    return '{}/proposals/{}/required_documents/{}'.format(settings.MEDIA_APP_DIR, instance.proposal.id,filename)

def update_requirement_doc_filename(instance, filename):
    return '{}/proposals/{}/requirement_documents/{}'.format(settings.MEDIA_APP_DIR, instance.requirement.proposal.id,filename)

def update_proposal_comms_log_filename(instance, filename):
    return '{}/proposals/{}/communications/{}/{}'.format(settings.MEDIA_APP_DIR, instance.log_entry.proposal.id, instance.log_entry.id, filename)

def update_vessel_comms_log_filename(instance, filename):
    return '{}/vessels/{}/communications/{}/{}'.format(settings.MEDIA_APP_DIR, instance.log_entry.vessel.id, instance.log_entry.id, filename)

def update_mooring_comms_log_filename(instance, filename):
    return '{}/moorings/{}/communications/{}/{}'.format(settings.MEDIA_APP_DIR, instance.log_entry.mooring.id, instance.log_entry.id, filename)


class ProposalDocument(Document):
    proposal = models.ForeignKey('Proposal',related_name='documents', on_delete=models.CASCADE)
    _file = models.FileField(storage=private_storage,upload_to=update_proposal_doc_filename, max_length=512)
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide= models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden=models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Application Document"


class RequirementDocument(Document):
    requirement = models.ForeignKey('ProposalRequirement',related_name='requirement_documents', on_delete=models.CASCADE)
    _file = models.FileField(storage=private_storage,upload_to=update_requirement_doc_filename, max_length=512)
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    visible = models.BooleanField(default=True) # to prevent deletion on file system, hidden and still be available in history

    def delete(self):
        if self.can_delete:
            return super(RequirementDocument, self).delete()


VESSEL_TYPES = (
        ('catamaran', 'Catamaran'),
        ('bow_rider', 'Bow Rider'),
        ('cabin_ruiser', 'Cabin Cruiser'),
        ('centre_console', 'Centre Console'),
        ('ferry', 'Ferry'),
        ('rigid_inflatable', 'Rigid Inflatable'),
        ('half_cabin', 'Half Cabin'),
        ('inflatable', 'Inflatable'),
        ('launch', 'Launch'),
        ('motor_sailer', 'Motor Sailer'),
        ('multihull', 'Multihull'),
        ('open_boat', 'Open Boat'),
        ('power_boat', 'Power Boat'),
        ('pwc', 'PWC'),
        ('Runabout', 'Runabout'),
        ('fishing_boat', 'Fishing Boat'),
        ('tender', 'Tender'),
        ('walkaround', 'Walkaround'),
        ('other', 'Other'),
    )

INSURANCE_CHOICES = (
    ("five_million", "$5 million Third Party Liability insurance cover - required for vessels of length less than 6.4 metres"),
    ("ten_million", "$10 million Third Party Liability insurance cover - required for vessels of length 6.4 metres or greater"),
    ("over_ten", "over $10 million"),
)
MOORING_AUTH_PREFERENCES = (
        ('site_licensee', 'By a mooring site licensee for their mooring'),
        ('ria', 'By Rottnest Island Authority for a mooring allocated by the Authority'),
        )


class ProposalType(RevisionedMixin):
    code = models.CharField(max_length=30, blank=True, null=True)
    description = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        # return 'id: {} code: {}'.format(self.id, self.code)
        return self.description if self.description else ''

    class Meta:
        app_label = 'mooringlicensing'


# class ProposalApplicantDetails(models.Model):
#     '''
#     This model is for storing the historical applicant details
#     '''
#     details = models.JSONField(blank=True, null=True)
#
#     class Meta:
#         app_label = 'mooringlicensing'

class ProposalProofOfIdentityDocument(models.Model):
    proof_of_identity_document = models.ForeignKey('ProofOfIdentityDocument', on_delete=models.CASCADE)
    proposal = models.ForeignKey('Proposal', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = 'mooringlicensing'


class ProposalMooringReportDocument(models.Model):
    mooring_report_document = models.ForeignKey('MooringReportDocument', on_delete=models.CASCADE)
    proposal = models.ForeignKey('Proposal', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = 'mooringlicensing'


class ProposalWrittenProofDocument(models.Model):
    written_proof_document = models.ForeignKey('WrittenProofDocument', on_delete=models.CASCADE)
    proposal = models.ForeignKey('Proposal', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = 'mooringlicensing'


class ProposalSignedLicenceAgreementDocument(models.Model):
    signed_licence_agreement_document = models.ForeignKey('SignedLicenceAgreementDocument', on_delete=models.CASCADE)
    proposal = models.ForeignKey('Proposal', on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)

    class Meta:
        app_label = 'mooringlicensing'


class Proposal(DirtyFieldsMixin, RevisionedMixin):
    APPLICANT_TYPE_ORGANISATION = 'ORG'
    APPLICANT_TYPE_PROXY = 'PRX'
    APPLICANT_TYPE_SUBMITTER = 'SUB'

    CUSTOMER_STATUS_DRAFT = 'draft'
    CUSTOMER_STATUS_WITH_ASSESSOR = 'with_assessor'
    CUSTOMER_STATUS_WITH_APPROVER = 'with_approver'
    CUSTOMER_STATUS_AWAITING_ENDORSEMENT = 'awaiting_endorsement'
    CUSTOMER_STATUS_AWAITING_DOCUMENTS = 'awaiting_documents'
    CUSTOMER_STATUS_STICKER_TO_BE_RETURNED = 'sticker_to_be_returned'
    CUSTOMER_STATUS_PRINTING_STICKER = 'printing_sticker'
    CUSTOMER_STATUS_APPROVED = 'approved'
    CUSTOMER_STATUS_DECLINED = 'declined'
    CUSTOMER_STATUS_DISCARDED = 'discarded'
    CUSTOMER_STATUS_AWAITING_PAYMENT = 'awaiting_payment'
    CUSTOMER_STATUS_EXPIRED = 'expired'
    CUSTOMER_STATUS_CHOICES = (
        (CUSTOMER_STATUS_DRAFT, 'Draft'),
        (CUSTOMER_STATUS_WITH_ASSESSOR, 'Under Review'),
        (CUSTOMER_STATUS_WITH_APPROVER, 'Under Review'),
        (CUSTOMER_STATUS_AWAITING_ENDORSEMENT, 'Awaiting Endorsement'),
        (CUSTOMER_STATUS_AWAITING_DOCUMENTS, 'Awaiting Documents'),
        (CUSTOMER_STATUS_STICKER_TO_BE_RETURNED, 'Sticker to be Returned'),
        (CUSTOMER_STATUS_PRINTING_STICKER, 'Printing Sticker'),
        (CUSTOMER_STATUS_APPROVED, 'Approved'),
        (CUSTOMER_STATUS_DECLINED, 'Declined'),
        (CUSTOMER_STATUS_DISCARDED, 'Discarded'),
        (CUSTOMER_STATUS_AWAITING_PAYMENT, 'Awaiting Payment'),
        (CUSTOMER_STATUS_EXPIRED, 'Expired'),
    )

    # List of statuses from above that allow a customer to edit an application.
    CUSTOMER_EDITABLE_STATE = [
        CUSTOMER_STATUS_DRAFT,
    ]

    # List of statuses from above that allow a customer to view an application (read-only)
    CUSTOMER_VIEWABLE_STATE = [
        CUSTOMER_STATUS_WITH_ASSESSOR,
        CUSTOMER_STATUS_WITH_APPROVER,
        CUSTOMER_STATUS_AWAITING_PAYMENT,
        CUSTOMER_STATUS_STICKER_TO_BE_RETURNED,
        CUSTOMER_STATUS_PRINTING_STICKER,
        CUSTOMER_STATUS_AWAITING_ENDORSEMENT,
        CUSTOMER_STATUS_AWAITING_DOCUMENTS,
        CUSTOMER_STATUS_APPROVED,
        CUSTOMER_STATUS_DECLINED,
        CUSTOMER_STATUS_EXPIRED,
    ]

    PROCESSING_STATUS_DRAFT = 'draft'
    PROCESSING_STATUS_WITH_ASSESSOR = 'with_assessor'
    PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS = 'with_assessor_requirements'
    PROCESSING_STATUS_WITH_APPROVER = 'with_approver'
    PROCESSING_STATUS_STICKER_TO_BE_RETURNED = 'sticker_to_be_returned'
    PROCESSING_STATUS_PRINTING_STICKER = 'printing_sticker'
    PROCESSING_STATUS_AWAITING_ENDORSEMENT = 'awaiting_endorsement'
    PROCESSING_STATUS_AWAITING_DOCUMENTS = 'awaiting_documents'
    PROCESSING_STATUS_APPROVED = 'approved'
    PROCESSING_STATUS_DECLINED = 'declined'
    PROCESSING_STATUS_DISCARDED = 'discarded'
    PROCESSING_STATUS_AWAITING_PAYMENT = 'awaiting_payment'
    PROCESSING_STATUS_EXPIRED = 'expired'

    PROCESSING_STATUS_CHOICES = (
        (PROCESSING_STATUS_DRAFT, 'Draft'),
        (PROCESSING_STATUS_WITH_ASSESSOR, 'With Assessor'),
        (PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS, 'With Assessor (Requirements)'),
        (PROCESSING_STATUS_WITH_APPROVER, 'With Approver'),
        (PROCESSING_STATUS_STICKER_TO_BE_RETURNED, 'Sticker to be Returned'),
        (PROCESSING_STATUS_PRINTING_STICKER, 'Printing Sticker'),
        (PROCESSING_STATUS_AWAITING_ENDORSEMENT, 'Awaiting Endorsement'),
        (PROCESSING_STATUS_AWAITING_DOCUMENTS, 'Awaiting Documents'),
        (PROCESSING_STATUS_APPROVED, 'Approved'),
        (PROCESSING_STATUS_DECLINED, 'Declined'),
        (PROCESSING_STATUS_DISCARDED, 'Discarded'),
        (PROCESSING_STATUS_AWAITING_PAYMENT, 'Awaiting Payment'),
        (PROCESSING_STATUS_EXPIRED, 'Expired'),
    )

    proposal_type = models.ForeignKey(ProposalType, blank=True, null=True, on_delete=models.SET_NULL)

    assessor_data = JSONField(blank=True, null=True)
    comment_data = JSONField(blank=True, null=True)
    proposed_issuance_approval = JSONField(blank=True, null=True)

    customer_status = models.CharField('Customer Status', max_length=40, choices=CUSTOMER_STATUS_CHOICES,
                                       default=CUSTOMER_STATUS_CHOICES[0][0])
    org_applicant = models.ForeignKey(
        Organisation,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='org_applications') # not currently used in ML
    lodgement_number = models.CharField(max_length=9, blank=True, default='')
    lodgement_sequence = models.IntegerField(blank=True, default=0)
    lodgement_date = models.DateTimeField(blank=True, null=True)

    # proxy_applicant = models.ForeignKey(EmailUser, blank=True, null=True, related_name='mooringlicensing_proxy', on_delete=models.SET_NULL) # not currently used by ML
    proxy_applicant = models.IntegerField(blank=True, null=True) # not currently used by ML
    # submitter = models.ForeignKey(EmailUser, blank=True, null=True, related_name='mooringlicensing_proposals', on_delete=models.SET_NULL)
    submitter = models.IntegerField(blank=True, null=True)

    # assigned_officer = models.ForeignKey(EmailUser, blank=True, null=True, related_name='mooringlicensing_proposals_assigned', on_delete=models.SET_NULL)
    assigned_officer = models.IntegerField(blank=True, null=True)
    # assigned_approver = models.ForeignKey(EmailUser, blank=True, null=True, related_name='mooringlicensing_proposals_approvals', on_delete=models.SET_NULL)
    assigned_approver = models.IntegerField(blank=True, null=True)
    processing_status = models.CharField('Processing Status', max_length=40, choices=PROCESSING_STATUS_CHOICES,
                                         default=PROCESSING_STATUS_CHOICES[0][0])
    prev_processing_status = models.CharField(max_length=40, blank=True, null=True)

    approval = models.ForeignKey('mooringlicensing.Approval',null=True,blank=True, on_delete=models.SET_NULL)
    previous_application = models.ForeignKey('self', on_delete=models.SET_NULL, blank=True, null=True, related_name="succeeding_proposals")

    proposed_decline_status = models.BooleanField(default=False)
    title = models.CharField(max_length=255,null=True,blank=True)
    approval_level = models.CharField('Activity matrix approval level', max_length=255,null=True,blank=True)
    approval_level_document = models.ForeignKey(ProposalDocument, blank=True, null=True, related_name='approval_level_document', on_delete=models.SET_NULL)
    approval_comment = models.TextField(blank=True)
    #If the proposal is created as part of migration of approvals
    migrated=models.BooleanField(default=False)
    vessel_details = models.ForeignKey('VesselDetails', blank=True, null=True, on_delete=models.SET_NULL)
    vessel_ownership = models.ForeignKey('VesselOwnership', blank=True, null=True, on_delete=models.SET_NULL)
    # draft proposal status VesselDetails records - goes to VesselDetails master record after submit
    rego_no = models.CharField(max_length=200, blank=True, null=True)
    vessel_id = models.IntegerField(null=True,blank=True)
    vessel_type = models.CharField(max_length=20, choices=VESSEL_TYPES, blank=True)
    vessel_name = models.CharField(max_length=400, blank=True)
    vessel_length = models.DecimalField(max_digits=8, decimal_places=2, null=True) # does not exist in MB
    vessel_draft = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    vessel_beam = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    vessel_weight = models.DecimalField(max_digits=8, decimal_places=2, null=True) # tonnage
    berth_mooring = models.CharField(max_length=200, blank=True)
    # only for draft status proposals, otherwise retrieve from within vessel_ownership
    dot_name = models.CharField(max_length=200, blank=True, null=True)
    percentage = models.IntegerField(null=True, blank=True)
    individual_owner = models.NullBooleanField()
    company_ownership_percentage = models.IntegerField(null=True, blank=True)
    company_ownership_name = models.CharField(max_length=200, blank=True, null=True)
    ## Insurance component field
    insurance_choice = models.CharField(max_length=20, choices=INSURANCE_CHOICES, blank=True)
    ## WLA
    preferred_bay = models.ForeignKey('MooringBay', null=True, blank=True, on_delete=models.SET_NULL)
    ## Electoral Roll component field
    silent_elector = models.NullBooleanField() # if False, user is on electoral roll
    ## Mooring Authorisation fields mooring_suthorisation_preferences, bay_preferences_numbered, site_licensee_email and mooring
    # AUA
    mooring_authorisation_preference = models.CharField(max_length=20, choices=MOORING_AUTH_PREFERENCES, blank=True)
    bay_preferences_numbered = ArrayField(
            models.IntegerField(null=True, blank=True),
            blank=True,null=True,
            )
    site_licensee_email = models.CharField(max_length=200, blank=True, null=True)
    mooring = models.ForeignKey('Mooring', null=True, blank=True, on_delete=models.SET_NULL)
    endorser_reminder_sent = models.BooleanField(default=False)
    declined_by_endorser = models.BooleanField(default=False)
    ## MLA
    allocated_mooring = models.ForeignKey('Mooring', null=True, blank=True, on_delete=models.SET_NULL, related_name="ria_generated_proposal")
    waiting_list_allocation = models.ForeignKey('mooringlicensing.WaitingListAllocation',null=True,blank=True, related_name="ria_generated_proposal", on_delete=models.SET_NULL)
    date_invited = models.DateField(blank=True, null=True)  # The date RIA has invited the WLAllocation holder.  This application is expired in a configurable number of days after the invitation without submit.
    invitee_reminder_sent = models.BooleanField(default=False)
    temporary_document_collection_id = models.IntegerField(blank=True, null=True)
    # MLA documents
    proof_of_identity_documents = models.ManyToManyField('ProofOfIdentityDocument', through=ProposalProofOfIdentityDocument)
    mooring_report_documents = models.ManyToManyField('MooringReportDocument', through=ProposalMooringReportDocument)
    written_proof_documents = models.ManyToManyField('WrittenProofDocument', through=ProposalWrittenProofDocument)
    signed_licence_agreement_documents = models.ManyToManyField('SignedLicenceAgreementDocument', through=ProposalSignedLicenceAgreementDocument)

    # AUA amendment
    listed_moorings = models.ManyToManyField('Mooring', related_name='listed_on_proposals')
    keep_existing_mooring = models.BooleanField(default=True)
    # MLA amendment
    listed_vessels = models.ManyToManyField('VesselOwnership', 'listed_on_proposals')
    keep_existing_vessel = models.BooleanField(default=True)

    fee_season = models.ForeignKey('FeeSeason', null=True, blank=True, on_delete=models.SET_NULL)  # In some case, proposal doesn't have any fee related objects.  Which results in the impossibility to retrieve season, start_date, end_date, etc.
                                                                        # To avoid that, this fee_season field is used in order to store those data.
    auto_approve = models.BooleanField(default=False)
    null_vessel_on_create = models.BooleanField(default=True)
    personal_details = models.JSONField(null=True, blank=True)


    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Application"
        verbose_name_plural = "Applications"
    
    def get_latest_vessel_ownership_by_vessel(self, vessel):
        if self.previous_application:
            if self.previous_application.vessel_ownership:
                if self.previous_application.vessel_ownership.vessel == vessel:
                    # Same vessel is found.
                    # TODO: Check if vessel_ownership is valid
                    return self.previous_application.vessel_ownership
                else:
                    # vessel of the previous application is differenct vessel.  Search further back.
                    return self.previous_application.get_latest_vessel_ownership_by_vessel(vessel)
            else:
                # vessel_ownership is None or so (Null vessel case).  Search further back.
                return self.previous_application.get_latest_vessel_ownership_by_vessel(vessel)
        else:
            # No previous application exists
            return None

    def copy_proof_of_identity_documents(self, proposal):
        for doc in self.proof_of_identity_documents.all():
            link_item = ProposalProofOfIdentityDocument.objects.get(proposal=self, proof_of_identity_document=doc)
            if link_item.enabled:
                # Create link to the proposal only when the doc is not deleted.
                ProposalProofOfIdentityDocument.objects.create(proposal=proposal, proof_of_identity_document=doc)

    def copy_mooring_report_documents(self, proposal):
        for doc in self.mooring_report_documents.all():
            link_item = ProposalMooringReportDocument.objects.get(proposal=self, mooring_report_document=doc)
            if link_item.enabled:
                # Create link to the proposal only when the doc is not deleted.
                ProposalMooringReportDocument.objects.create(proposal=proposal, mooring_report_document=doc)

    def copy_written_proof_documents(self, proposal):
        for doc in self.written_proof_documents.all():
            link_item = ProposalWrittenProofDocument.objects.get(proposal=self, written_proof_document=doc)
            if link_item.enabled:
                # Create link to the proposal only when the doc is not deleted.
                ProposalWrittenProofDocument.objects.create(proposal=proposal, written_proof_document=doc)

    def copy_signed_licence_agreement_documents(self, proposal):
        for doc in self.signed_licence_agreement_documents.all():
            link_item = ProposalSignedLicenceAgreementDocument.objects.get(proposal=self, signed_licence_agreement_document=doc)
            if link_item.enabled:
                # Create link to the proposal only when the doc is not deleted.
                ProposalSignedLicenceAgreementDocument.objects.create(proposal=proposal, signed_licence_agreement_document=doc)

    def __str__(self):
        return str(self.lodgement_number)

    def withdraw(self, request, *args, **kwargs):
        self.processing_status = Proposal.PROCESSING_STATUS_DISCARDED
        self.save()
        logger.info(f'Status: [{self.processing_status}] has been set to the proposal: [{self}].')
        self.log_user_action(ProposalUserAction.ACTION_WITHDRAW_PROPOSAL.format(self.lodgement_number), request)

        # Perform post-processing for each application type after discarding.
        self.child_obj.process_after_withdrawn()

    def destroy(self, request, *args, **kwargs):
        self.processing_status = Proposal.PROCESSING_STATUS_DISCARDED
        self.save()
        logger.info(f'Status: [{self.processing_status}] has been set to the proposal: [{self}].')
        self.log_user_action(ProposalUserAction.ACTION_DISCARD_PROPOSAL.format(self.lodgement_number), request)

        # Perform post-processing for each application type after discarding.
        self.child_obj.process_after_discarded()

        # Send email
        send_application_discarded_email(self, request)

    def copy_vessel_details(self, proposal):
        proposal.rego_no = self.rego_no
        proposal.vessel_id = self.vessel_id
        proposal.vessel_type = self.vessel_type
        proposal.vessel_name = self.vessel_name
        proposal.vessel_length = self.vessel_length
        proposal.vessel_draft = self.vessel_draft
        proposal.vessel_beam = self.vessel_beam
        proposal.vessel_weight = self.vessel_weight
        proposal.berth_mooring = self.berth_mooring

        proposal.dot_name = self.dot_name
        proposal.percentage = self.percentage
        proposal.individual_owner = self.individual_owner
        proposal.company_ownership_percentage = self.company_ownership_percentage
        proposal.company_ownership_name = self.company_ownership_name

        proposal.save()

    def get_previous_vessel_ownerships(self):
        vessel_ownerships = []
        get_out_of_loop = False

        if self.proposal_type.code in [PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL,]:
            # When the proposal being processed is an amendment/renewal application,
            # we want to exclude the ownership percentages from the previous applications.
            proposal = self

            while True:
                if proposal.previous_application:
                    if proposal.previous_application.vessel_ownership:
                        if proposal.previous_application.vessel_ownership.excludable(proposal):
                            vessel_ownerships.append(proposal.previous_application.vessel_ownership)

                if get_out_of_loop:
                    break

                # Retrieve the previous application
                proposal = proposal.previous_application

                if not proposal:
                    # No previous application exists.  Get out of the loop
                    break
                else:
                    # Previous application exists
                    if proposal.proposal_type.code in [PROPOSAL_TYPE_NEW,]:
                        # Previous application is 'new'/'renewal'
                        # In this case, we don't want to go back any further once this proposal is processed in the next loop.  Therefore we set the flat to True
                        get_out_of_loop = True

        return vessel_ownerships

    @property
    def submitter_obj(self):
        return retrieve_email_userro(self.submitter) if self.submitter else None

    def get_fee_amount_adjusted(self, fee_item_being_applied, vessel_length, max_amount_paid):
        """
        Retrieve all the fee_items for this vessel
        """
        logger.info(f'Adjusting the fee amount for proposal: [{self}], fee_item: [{fee_item_being_applied}], vessel_length: [{vessel_length}]')
        if not fee_item_being_applied:
            msg = f'FeeItem is None.  Cannot proceed to calculate the fee_amount_adjusted for the proposal: [{self}]...'
            logger.exception(msg)
            raise ValidationError(msg)

        fee_amount_adjusted = fee_item_being_applied.get_absolute_amount(vessel_length)

        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)
        if self.proposal_type.code in (PROPOSAL_TYPE_AMENDMENT,) or fee_item_being_applied.application_type == annual_admission_type:
            # When amendment or adjusting an AA component, amount needs to be adjusted
            logger.info(f'Deduct $[{max_amount_paid}] from $[{fee_amount_adjusted}]')
            fee_amount_adjusted = fee_amount_adjusted - max_amount_paid
            logger.info(f'Result amount: $[{fee_amount_adjusted}]')

            fee_amount_adjusted = Decimal('0.00') if fee_amount_adjusted <= 0 else fee_amount_adjusted
        else:
            pass

        return fee_amount_adjusted

    def get_max_amount_paid_for_main_component(self):
        logger.info(f'Calculating the max amount paid for the main component...')
        max_amount_paid_for_main_component = 0

        if self.proposal_type.code not in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL]:
            prev_application = self.previous_application
            max_amounts_paid = self.get_amounts_paid_so_far(prev_application)  # None: we don't mind vessel for main component
            if self.application_type in max_amounts_paid:
                # When there is an AAP component
                if max_amount_paid_for_main_component < max_amounts_paid[self.application_type]:
                    # Update variable
                    max_amount_paid_for_main_component = max_amounts_paid[self.application_type]

        return max_amount_paid_for_main_component

    def get_max_amount_paid_for_aa_component(self, target_date, vessel):
        logger.info(f'Calculating the max amount paid for the AA component, which can be transferred for the vessel: [{vessel}]...')

        max_amount_paid_for_aa_component = 0

        if self.proposal_type.code not in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL]:
            # Get max amount for AA from this proposal history
            max_amount_paid = self.get_amount_paid_so_far_for_aa_through_this_proposal(self.previous_application, vessel)  # TODO: Fix, this amount might be paid for another vessel.
            if max_amount_paid_for_aa_component < max_amount_paid:
                max_amount_paid_for_aa_component = max_amount_paid

            # Get max amount for this vessel from other current/suspended approvals
            current_approvals = vessel.get_current_aaps(target_date)
            for approval in current_approvals:
                # Current approval exists
                max_amount_paid = self.get_amounts_paid_so_far_for_aa_through_other_approvals(approval.current_proposal, vessel)  # We mind vessel for AA component
                # if annual_admission_type in max_amounts_paid:
                # When there is an AAP component
                if max_amount_paid_for_aa_component < max_amount_paid:
                    # Update variable
                    max_amount_paid_for_aa_component = max_amount_paid

        return max_amount_paid_for_aa_component

    def payment_required(self):
        payment_required = False
        if self.application_fees and self.application_fees.count():
            application_fee = self.get_main_application_fee()
            invoice = Invoice.objects.get(reference=application_fee.invoice_reference)
            # if invoice.payment_status not in ('paid', 'over_paid'):
            if get_invoice_payment_status(invoice.id) not in ('paid', 'over_paid'):
                payment_required = True
        return payment_required

    def get_amount_paid_so_far_for_aa_through_this_proposal(self, proposal, vessel):
        logger.info(f'Calculating the amount paid so far for the AA component through the proposal(s) which leads to the proposal: [{self}]...')

        target_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        target_date = target_datetime.date()
        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)

        max_amount_paid = 0
        max_count = 50  # To avoid infinite loop, set max number of iterations
        loop_count = 0
        while loop_count <= max_count:
            loop_count += 1
            if proposal:
                for application_fee in proposal.application_fees.all():
                    for fee_item_application_fee in application_fee.feeitemapplicationfee_set.all():
                        if fee_item_application_fee.application_type == annual_admission_type:
                            # We are interested only in the AnnualAdmission component
                            logger.info(f'FeeItemApplicationFee: [{fee_item_application_fee}] found through the proposal: [{proposal}]')

                            target_vessel = fee_item_application_fee.vessel_details.vessel

                            # Retrieve the current approvals of the target_vessel
                            current_approvals = target_vessel.get_current_approvals(target_date)
                            logger.info(f'Current approvals for the vessel: [{target_vessel}]: {current_approvals}')

                            if target_vessel != vessel:
                                from mooringlicensing.components.approvals.models import MooringLicence, AuthorisedUserPermit

                                if proposal.approval and proposal.approval.child_obj and type(proposal.approval.child_obj) == MooringLicence:
                                    # When ML, customer is adding a new vessel to the ML
                                    if not current_approvals['aaps'] and not current_approvals['aups'] and not current_approvals['mls']:
                                        # However, old vessel (target vessel) is no longer on any licence/permit.
                                        logger.info(f'Vessel: [{vessel}] is being added to the approval: [{proposal.approval}], however the vessel is no longer on any permit/licence.  We can transfer the amount paid: [{fee_item_application_fee}].')
                                    else:
                                        # We have to charge full amount  --> Go to next loop
                                        logger.info(f'Vessel: [{vessel}] is being added to the approval: [{proposal.approval}] and the vessel: [{target_vessel}] is still on another licence/permit.  We cannot transfer the amount paid: [{fee_item_application_fee}] for the vessel: [{vessel}].')
                                        continue
                                if proposal.approval and proposal.approval.child_obj and type(proposal.approval.child_obj) == AuthorisedUserPermit:
                                    # When AU, customer is replacing the current vessel
                                    for key, qs in current_approvals.items():
                                        # We want to exclude the approval being amended(modified) because the target_vessel is being removed from it.
                                        current_approvals[key] = qs.exclude(id=self.approval.id)
                                    if current_approvals['aaps'] or current_approvals['aups'] or current_approvals['mls']:
                                        # When the current vessel is still used for other approvals --> Go to next loop
                                        # But this fee_item_application_fee is still used for other approval(s)
                                        logger.info(f'Existing Vessel: [{target_vessel}] still has current approval(s): [{current_approvals}].  We don\'t transfer the amount paid: [{fee_item_application_fee}].')
                                        continue

                            # This is paid for AA component for a target_vessel, but that vessel is no longer on any permit/licence
                            # In this case, we can transfer this amount
                            amount_paid = fee_item_application_fee.amount_paid
#                                if max_amount_paid < amount_paid:
#                                    # The amount paid found is larger than the one stored, update it.
#                                    max_amount_paid = amount_paid
                            max_amount_paid += amount_paid
                            logger.info(f'Transferable amount: [{fee_item_application_fee}], which already has been paid.')

                if proposal.proposal_type.code in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL, ]:
                    # Now, 'prev_application' is the very first application for this season
                    # We are not interested in any older applications
                    break
                else:
                    # Assign the previous application, then perform checking above again
                    proposal = proposal.previous_application
            else:
                break
        return max_amount_paid

    def get_amounts_paid_so_far(self, proposal):
        max_amounts_paid = {
            ApplicationType.objects.get(code=WaitingListApplication.code): Decimal('0.0'),
            ApplicationType.objects.get(code=AnnualAdmissionApplication.code): Decimal('0.0'),
            ApplicationType.objects.get(code=AuthorisedUserApplication.code): Decimal('0.0'),
            ApplicationType.objects.get(code=MooringLicenceApplication.code): Decimal('0.0'),
        }
        max_count = 50  # To avoid infinite loop, set max number of iterations
        loop_count = 0
        while loop_count <= max_count:
            loop_count += 1
            if proposal:
                for application_fee in proposal.application_fees.all():
                    for fee_item_application_fee in application_fee.feeitemapplicationfee_set.all():
                        # When not for AAP component
                        # or for AAP component and fee_item paid is for this vessel
                        amount_paid = fee_item_application_fee.amount_paid
#                        if max_amounts_paid[fee_item_application_fee.application_type] < amount_paid:
#                            # The amount paid found is larger than the one stored, update it.
#                            max_amounts_paid[fee_item_application_fee.application_type] = amount_paid
                        max_amounts_paid[fee_item_application_fee.application_type] += amount_paid
                if proposal.proposal_type.code in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL, ]:
                    # Now, 'prev_application' is the very first application for this season
                    # We are not interested in any older applications
                    break
                else:
                    # Assign the previous application, then perform checking above again
                    proposal = proposal.previous_application
            else:
                break
        return max_amounts_paid

    def get_amounts_paid_so_far_for_aa_through_other_approvals(self, proposal, vessel):
        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)

        max_amount_paid = 0
        max_count = 50  # To avoid infinite loop, set max number of iterations
        loop_count = 0
        while loop_count <= max_count:
            loop_count += 1
            if proposal:
                for application_fee in proposal.application_fees.all():
                    for fee_item_application_fee in application_fee.feeitemapplicationfee_set.all():
                        if fee_item_application_fee.application_type == annual_admission_type and fee_item_application_fee.vessel_details.vessel == vessel:
                            # When not for AAP component
                            # or for AAP component and fee_item paid is for this vessel
                            amount_paid = fee_item_application_fee.amount_paid
#                            if max_amount_paid < amount_paid:
#                                # The amount paid found is larger than the one stored, update it.
#                                max_amount_paid = amount_paid
                            max_amount_paid += amount_paid
                if proposal.proposal_type.code in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL, ]:
                    # Now, 'prev_application' is the very first application for this season
                    # We are not interested in any older applications
                    break
                else:
                    # Assign the previous application, then perform checking above again
                    proposal = proposal.previous_application
            else:
                break
        return max_amount_paid

    @property
    def latest_vessel_details(self):
        if self.vessel_ownership:
            return self.vessel_ownership.vessel.latest_vessel_details
        else:
            return None

    def invoices_display(self):
        ret_list = []
        invoice_references = [item.invoice_reference for item in self.application_fees.filter(system_invoice=False)]
        return Invoice.objects.filter(reference__in=invoice_references)

    @staticmethod
    def get_corresponding_amendment_fee_item(accept_null_vessel, fee_constructor, fee_item, target_date, vessel_length):
        proposal_type_amendment = ProposalType.objects.get(code=PROPOSAL_TYPE_AMENDMENT)
        if fee_item.proposal_type.code == PROPOSAL_TYPE_AMENDMENT:
            # This application is 'Amendment' application.  fee_item is already for 'Amendment'
            fee_item_amendment_calculation = fee_item
        else:
            # We want to store the fee_item considered to be paid in order to calculate the amount for the amendment application
            fee_item_amendment_calculation = fee_constructor.get_fee_item(vessel_length, proposal_type_amendment,
                                                                          target_date,
                                                                          accept_null_vessel=accept_null_vessel)
        return fee_item_amendment_calculation

    def get_fee_items_paid(self, fee_season, vessel_details=None):
        from mooringlicensing.components.payments_ml.models import FeeItemApplicationFee

        fee_items = []

        queries = Q()
        queries &= Q(application_fee__in=self.application_fees.all())
        queries &= Q(fee_item__fee_period__fee_season=fee_season)
        if vessel_details:
            # AA component for ML, we mind the vessel
            queries &= Q(vessel_details__vessel=vessel_details.vessel)

        fee_item_application_fees = FeeItemApplicationFee.objects.filter(queries)

        for fee_item_application_fee in fee_item_application_fees:
            fee_items.append(fee_item_application_fee.fee_item)

        return fee_items

    @property
    def fee_period(self):
        fee_items = self.get_fee_items_paid()
        if len(fee_items):
            fee_item = fee_items[0]
            return fee_item.fee_period

    # @property
    # def different_sticker_colour(self):
    #     from mooringlicensing.components.approvals.models import Sticker
    #     next_colour = Sticker.get_vessel_size_colour_by_length(self.vessel_length)
    #     current_colour = Sticker.get_vessel_size_colour_by_length(self.previous_application.vessel_length)
    #     return True if next_colour == current_colour else False

    @property
    def vessel_removed(self):
        # for AUP, AAP manage_stickers
        if type(self) is Proposal:
            if type(self.child_obj) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        else:
            if type(self) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        removed = False
        if (
                self.previous_application and
                self.previous_application.vessel_ownership and
                not self.previous_application.vessel_ownership.end_date and  # There was a vessel in the previous application and not sold
                (not self.vessel_ownership or self.vessel_ownership.end_date)
        ):
            removed = True
        return removed

    @property
    def vessel_swapped(self):
        # for AUP, AAP manage_stickers
        if type(self) is Proposal:
            if type(self.child_obj) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        else:
            if type(self) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        # if type(self.child_obj) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
        #     raise ValidationError("Only for AUP, AAA")
        changed = False
        if (
                self.previous_application and
                self.previous_application.vessel_ownership and
                not self.previous_application.vessel_ownership.end_date and  # Not sold yet
                self.vessel_ownership and
                not self.vessel_ownership.end_date and  # Not sold yet
                self.vessel_ownership.vessel.rego_no != self.previous_application.vessel_ownership.vessel.rego_no
        ):
            changed = True
        return changed

    @property
    def vessel_null_to_new(self):
        # for AUP, AAP manage_stickers
        if type(self) is Proposal:
            if type(self.child_obj) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        else:
            if type(self) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
                raise ValidationError("Only for AUP, AAA")
        # if type(self.child_obj) not in [AuthorisedUserApplication, AnnualAdmissionApplication]:
        #     raise ValidationError("Only for AUP, AAA")
        new = False
        if (
                self.previous_application and
                (not self.previous_application.vessel_ownership or self.previous_application.vessel_ownership.end_date) and
                self.vessel_ownership and
                not self.vessel_ownership.end_date
        ):
            new = True
        return new

    def does_have_valid_associations(self):
        # Check if this application has valid associations with other applications and approvals
        return self.child_obj.does_have_valid_associations()

    @property
    def final_status(self):
        final_status = False
        if self.processing_status in ([Proposal.PROCESSING_STATUS_PRINTING_STICKER, Proposal.PROCESSING_STATUS_APPROVED]):
            final_status = True
        return final_status

    def endorse_approved(self, request):
        self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
        self.save()
        logger.info(f'Endorsement approved for the Proposal: [{self}] by the endorser: [{request.user}].')

    def endorse_declined(self, request):
        self.processing_status = Proposal.PROCESSING_STATUS_DECLINED
        self.declined_by_endorser = True
        self.save()
        logger.info(f'Endorsement declined for the Proposal: [{self}] by the endorser: [{request.user}].')

    def update_customer_status(self):
        matrix = {
            Proposal.PROCESSING_STATUS_DRAFT: Proposal.CUSTOMER_STATUS_DRAFT,
            Proposal.PROCESSING_STATUS_WITH_ASSESSOR: Proposal.CUSTOMER_STATUS_WITH_ASSESSOR,
            Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS: Proposal.CUSTOMER_STATUS_WITH_ASSESSOR,
            Proposal.PROCESSING_STATUS_WITH_APPROVER: Proposal.CUSTOMER_STATUS_WITH_APPROVER,
            Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED: Proposal.CUSTOMER_STATUS_STICKER_TO_BE_RETURNED,
            Proposal.PROCESSING_STATUS_PRINTING_STICKER: Proposal.CUSTOMER_STATUS_PRINTING_STICKER,
            Proposal.PROCESSING_STATUS_AWAITING_ENDORSEMENT: Proposal.CUSTOMER_STATUS_AWAITING_ENDORSEMENT,
            Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS: Proposal.CUSTOMER_STATUS_AWAITING_DOCUMENTS,
            Proposal.PROCESSING_STATUS_APPROVED: Proposal.CUSTOMER_STATUS_APPROVED,
            Proposal.PROCESSING_STATUS_DECLINED: Proposal.CUSTOMER_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_DISCARDED: Proposal.CUSTOMER_STATUS_DISCARDED,
            Proposal.PROCESSING_STATUS_AWAITING_PAYMENT: Proposal.CUSTOMER_STATUS_AWAITING_PAYMENT,
            Proposal.PROCESSING_STATUS_EXPIRED: Proposal.CUSTOMER_STATUS_EXPIRED,
        }
        self.customer_status = matrix[self.processing_status]

    def save(self, *args, **kwargs):
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        self.update_customer_status()
        super(Proposal, self).save(**kwargs)
        if type(self) == Proposal:
            self.child_obj.refresh_from_db()

    def get_main_application_fee(self):
        main_af = None
        for af in self.application_fees.all():
            if af.fee_constructor:
                main_af = af
                break
        logger.debug(f'Main ApplicationFee: [{main_af}] found for the Proposal: [{self}].')
        return main_af

    @property
    def fee_constructor(self):
        application_fee = self.get_main_application_fee()
        if application_fee:
            return application_fee.fee_constructor
        else:
            return None

    @property
    def invoice(self):
        invoice = None
        application_fee = self.get_main_application_fee()
        if application_fee:
            invoice = Invoice.objects.get(reference=application_fee.invoice_reference)
        return invoice

    @property
    def start_date(self):
        if self.migrated:
            return datetime.datetime(2020,9,1).date()

        start_date = None
        application_fee = self.get_main_application_fee()
        if application_fee:
            start_date = application_fee.fee_constructor.start_date
        elif self.fee_season:
            # application_fee can be None when there are no charges for this proposal i.e. AU doesn't charge anything when ML exists for the same vessel.
            start_date = self.fee_season.start_date
        return start_date

    @property
    def end_date(self):
        if self.migrated:
            return datetime.datetime(2021,11,30).date()

        end_date = None
        application_fee = self.get_main_application_fee()
        if application_fee:
            end_date = application_fee.fee_constructor.end_date
        elif self.fee_season:
            # application_fee can be None when there are no charges for this proposal i.e. AU doesn't charge anything when ML exists for the same vessel.
            end_date = self.fee_season.end_date
        return end_date

    @property
    def editable_vessel_details(self):
        editable = True
        return editable

    @property
    def fee_paid(self):
        # if (self.invoice and self.invoice.payment_status in ['paid', 'over_paid']) or self.proposal_type==PROPOSAL_TYPE_AMENDMENT:
        if (self.invoice and get_invoice_payment_status(self.invoice.id) in ['paid', 'over_paid']) or self.proposal_type==PROPOSAL_TYPE_AMENDMENT:
            return True
        return False

    @property
    def fee_amount(self):
        return self.invoice.amount if self.fee_paid else None

    @property
    def reversion_ids(self):
        current_revision_id = Version.objects.get_for_object(self).first().revision_id
        versions = Version.objects.get_for_object(self).select_related("revision__user").filter(Q(revision__comment__icontains='status') | Q(revision_id=current_revision_id))
        version_ids = [[i.id,i.revision.date_created] for i in versions]
        return [dict(cur_version_id=version_ids[0][0], prev_version_id=version_ids[i+1][0], created=version_ids[i][1]) for i in range(len(version_ids)-1)]

    @property
    def applicant(self):
        from mooringlicensing.ledger_api_utils import retrieve_email_userro

        if self.org_applicant:
            return self.org_applicant.organisation.name
        elif self.proxy_applicant:
            applicant = retrieve_email_userro(self.proxy_applicant)
            return "{} {}".format(
                applicant.first_name,
                applicant.last_name)
        else:
            applicant = retrieve_email_userro(self.submitter)
            return "{} {}".format(
                applicant.first_name,
                applicant.last_name)

    @property
    def applicant_email(self):
        from mooringlicensing.ledger_api_utils import retrieve_email_userro

        if self.org_applicant and hasattr(self.org_applicant.organisation, 'email') and self.org_applicant.organisation.email:
            return self.org_applicant.organisation.email
        elif self.proxy_applicant:
            applicant = retrieve_email_userro(self.proxy_applicant)
            return applicant.email
        else:
            # return self.submitter.email
            from mooringlicensing.ledger_api_utils import retrieve_email_userro
            return retrieve_email_userro(self.submitter).email

    @property
    def applicant_details(self):
        from mooringlicensing.ledger_api_utils import retrieve_email_userro

        if self.org_applicant:
            return '{} \n{}'.format(
                self.org_applicant.organisation.name,
                self.org_applicant.address)
        elif self.proxy_applicant:
            applicant = retrieve_email_userro(self.proxy_applicant)
            return "{} {}\n{}".format(
                applicant.first_name,
                applicant.last_name,
                applicant.addresses.all().first())
        else:
            applicant = retrieve_email_userro(self.submitter)
            test = applicant.addresses.all()
            return "{} {}\n{}".format(
                applicant.first_name,
                applicant.last_name,
                # applicant.addresses.all().first())
                applicant.residential_address)

    @property
    def applicant_address(self):
        from mooringlicensing.ledger_api_utils import retrieve_email_userro

        if self.org_applicant:
            return self.org_applicant.address
        elif self.proxy_applicant:
            applicant = retrieve_email_userro(self.proxy_applicant)
            return applicant.residential_address
        else:
            applicant = retrieve_email_userro(self.submitter)
            return applicant.residential_address

    @property
    def applicant_id(self):
        if self.org_applicant:
            return self.org_applicant.id
        elif self.proxy_applicant:
            return self.proxy_applicant.id
        else:
            return self.submitter.id

    @property
    def applicant_type(self):
        if self.org_applicant:
            return self.APPLICANT_TYPE_ORGANISATION
        elif self.proxy_applicant:
            return self.APPLICANT_TYPE_PROXY
        else:
            return self.APPLICANT_TYPE_SUBMITTER

    @property
    def applicant_field(self):
        if self.org_applicant:
            return 'org_applicant'
        elif self.proxy_applicant:
            return 'proxy_applicant'
        else:
            return 'submitter'

    @property
    def get_history(self):
        """ Return the prev proposal versions """
        l = []
        p = copy.deepcopy(self)
        while (p.previous_application):
            l.append( dict(id=p.previous_application.id, modified=p.previous_application.modified_date) )
            p = p.previous_application
        return l

    @property
    def is_assigned(self):
        return self.assigned_officer is not None

    @property
    def can_user_edit(self):
        """
        :return: True if the application is in one of the editable status.
        """
        return self.customer_status in self.CUSTOMER_EDITABLE_STATE

    @property
    def can_user_view(self):
        """
        :return: True if the application is in one of the approved status.
        """
        return self.customer_status in self.CUSTOMER_VIEWABLE_STATE

    @property
    def assessor_assessment(self):
        qs = self.assessment.all()  # <== Is this correct???
        if qs:
            return qs[0]
        else:
            return None

    @property
    def permit(self):
        return self.approval.licence_document._file.url if self.approval else None

    @property
    def allowed_assessors(self):
        if self.processing_status == 'with_approver':
            group = self.__approver_group()
        else:
            group = self.__assessor_group()
        # return group.user_set.all() if group else []
        ids = group.get_system_group_member_ids() if group else []
        users = EmailUserRO.objects.filter(id__in=ids)
        return users

    @property
    def compliance_assessors(self):
        group = self.__assessor_group()
        # return group.user_set.all() if group else []
        ids = group.get_system_group_member_ids() if group else []
        users = EmailUserRO.objects.filter(id__in=ids)
        return users

    def allowed_assessors_user(self, request):
        if self.processing_status == 'with_approver':
            group = self.__approver_group()
        else:
            group = self.__assessor_group()
        # return True if group and group.user_set.filter(id=request.user.id).values_list('id', flat=True) else False
        return True if group and request.user.id in group.get_system_group_member_ids() else False

    @property
    def can_officer_process(self):
        """ :return: True if the application is in one of the processable status for Assessor role."""
        officer_view_state = [
            Proposal.PROCESSING_STATUS_DRAFT,
            Proposal.PROCESSING_STATUS_APPROVED,
            Proposal.PROCESSING_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_DISCARDED,
            Proposal.PROCESSING_STATUS_AWAITING_PAYMENT,
            Proposal.PROCESSING_STATUS_AWAITING_ENDORSEMENT,
            Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS,
            Proposal.PROCESSING_STATUS_PRINTING_STICKER,
            Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED,
        ]
        return False if self.processing_status in officer_view_state else True

    @property
    def amendment_requests(self):
        qs =AmendmentRequest.objects.filter(proposal=self)
        return qs

    #Check if there is an pending amendment request exist for the proposal
    @property
    def pending_amendment_request(self):
        qs =AmendmentRequest.objects.filter(proposal = self, status = "requested")
        if qs:
            return True
        return False

    @property
    def is_amendment_proposal(self):
        if self.proposal_type==PROPOSAL_TYPE_AMENDMENT:
            return True
        return False

    def __assessor_group(self):
        return self.child_obj.assessor_group

    def __approver_group(self):
        return self.child_obj.approver_group

    def __check_proposal_filled_out(self):
        if not self.data:
            raise exceptions.ProposalNotComplete()
        missing_fields = []
        required_fields = {
        }
        for k,v in required_fields.items():
            val = getattr(self,k)
            if not val:
                missing_fields.append(v)
        return missing_fields

    @property
    def assessor_recipients(self):
        return self.child_obj.assessor_recipients

    @property
    def approver_recipients(self):
        return self.child_obj.approver_recipients

    #Check if the user is member of assessor group for the Proposal
    def is_assessor(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        return self.child_obj.is_assessor(user)

    #Check if the user is member of assessor group for the Proposal
    def is_approver(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        return self.child_obj.is_approver(user)

    def can_assess(self, user):
        if self.processing_status in [Proposal.PROCESSING_STATUS_WITH_ASSESSOR, Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS]:
            return self.child_obj.is_assessor(user)
        elif self.processing_status in [Proposal.PROCESSING_STATUS_WITH_APPROVER, Proposal.PROCESSING_STATUS_AWAITING_PAYMENT, Proposal.PROCESSING_STATUS_PRINTING_STICKER]:
            return self.child_obj.is_approver(user)
        else:
            return False

    def has_assessor_mode(self,user):
        # status_without_assessor = ['with_approver','approved','awaiting_payment','declined','draft']
        if isinstance(user, EmailUserRO):
            user = user.id

        status_without_assessor = [Proposal.PROCESSING_STATUS_WITH_APPROVER, Proposal.PROCESSING_STATUS_APPROVED, Proposal.PROCESSING_STATUS_AWAITING_PAYMENT, Proposal.PROCESSING_STATUS_DECLINED, Proposal.PROCESSING_STATUS_DRAFT]
        if self.processing_status in status_without_assessor:
            return False
        else:
            if self.assigned_officer:
                if self.assigned_officer == user:
                    return self.child_obj.is_assessor(user)
                else:
                    return False
            else:
                return self.child_obj.is_assessor(user)

    def log_user_action(self, action, request=None):
        if request:
            return ProposalUserAction.log_action(self, action, request.user.id)
        else:
            return ProposalUserAction.log_action(self, action)

    @property
    def is_submitted(self):
        return True if self.lodgement_date else False

    def update(self,request,viewset):
        from mooringlicensing.components.proposals.utils import save_proponent_data
        with transaction.atomic():
            if self.can_user_edit:
                # Save the data first
                save_proponent_data(self,request,viewset)
                self.save()
            else:
                raise ValidationError('You can\'t edit this proposal at this moment')

    def assign_officer(self, request, officer):
        with transaction.atomic():
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if not self.can_assess(officer):
                    raise ValidationError('The selected person is not authorised to be assigned to this proposal')
                if self.processing_status == 'with_approver':
                    if officer.id != self.assigned_approver:
                        self.assigned_approver = officer.id
                        self.save()
                        # Create a log entry for the proposal
                        self.log_user_action(ProposalUserAction.ACTION_ASSIGN_TO_APPROVER.format(self.id,'{}({})'.format(officer.get_full_name(),officer.email)),request)
                        # Create a log entry for the organisation
                        applicant_field=getattr(self, self.applicant_field)
                        # applicant_field.log_user_action(ProposalUserAction.ACTION_ASSIGN_TO_APPROVER.format(self.id,'{}({})'.format(officer.get_full_name(),officer.email)),request)
                else:
                    if officer.id != self.assigned_officer:
                        self.assigned_officer = officer.id
                        self.save()
                        # Create a log entry for the proposal
                        self.log_user_action(ProposalUserAction.ACTION_ASSIGN_TO_ASSESSOR.format(self.id,'{}({})'.format(officer.get_full_name(),officer.email)),request)
                        # Create a log entry for the organisation
                        applicant_field=getattr(self, self.applicant_field)
                        # applicant_field.log_user_action(ProposalUserAction.ACTION_ASSIGN_TO_ASSESSOR.format(self.id,'{}({})'.format(officer.get_full_name(),officer.email)),request)
            except:
                raise

    def assing_approval_level_document(self, request):
        with transaction.atomic():
            try:
                approval_level_document = request.data['approval_level_document']
                if approval_level_document != 'null':
                    try:
                        document = self.documents.get(input_name=str(approval_level_document))
                    except ProposalDocument.DoesNotExist:
                        document = self.documents.get_or_create(input_name=str(approval_level_document), name=str(approval_level_document))[0]
                    document.name = str(approval_level_document)
                    document._file = approval_level_document
                    document.save()
                    d=ProposalDocument.objects.get(id=document.id)
                    self.approval_level_document = d
                    comment = 'Approval Level Document Added: {}'.format(document.name)
                else:
                    self.approval_level_document = None
                    comment = 'Approval Level Document Deleted: {}'.format(request.data['approval_level_document_name'])
                self.save(version_comment=comment) # to allow revision to be added to reversion history
                self.log_user_action(ProposalUserAction.ACTION_APPROVAL_LEVEL_DOCUMENT.format(self.id),request)
                # Create a log entry for the organisation
                applicant_field=getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_APPROVAL_LEVEL_DOCUMENT.format(self.id),request)
                return self
            except:
                raise

    def unassign(self,request):
        with transaction.atomic():
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if self.processing_status == 'with_approver':
                    if self.assigned_approver:
                        self.assigned_approver = None
                        self.save()
                        # Create a log entry for the proposal
                        self.log_user_action(ProposalUserAction.ACTION_UNASSIGN_APPROVER.format(self.id),request)
                        # Create a log entry for the organisation
                        applicant_field=getattr(self, self.applicant_field)
                        # applicant_field.log_user_action(ProposalUserAction.ACTION_UNASSIGN_APPROVER.format(self.id),request)
                else:
                    if self.assigned_officer:
                        self.assigned_officer = None
                        self.save()
                        # Create a log entry for the proposal
                        self.log_user_action(ProposalUserAction.ACTION_UNASSIGN_ASSESSOR.format(self.id),request)
                        # Create a log entry for the organisation
                        applicant_field=getattr(self, self.applicant_field)
                        # applicant_field.log_user_action(ProposalUserAction.ACTION_UNASSIGN_ASSESSOR.format(self.id),request)
            except:
                raise

    def add_default_requirements(self):
        # Add default standard requirements to Proposal
        due_date = None
        default_requirements = ProposalStandardRequirement.objects.filter(application_type=self.application_type, default=True, obsolete=False)
        if default_requirements:
            for req in default_requirements:
                r, created = ProposalRequirement.objects.get_or_create(proposal=self, standard_requirement=req, due_date=due_date)

    def move_to_status(self, request, status, approver_comment):
        if not status:
            raise serializers.ValidationError('Status is required')
        if status not in [Proposal.PROCESSING_STATUS_WITH_ASSESSOR, Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS, Proposal.PROCESSING_STATUS_WITH_APPROVER]:
            raise serializers.ValidationError('The status provided is not allowed')
        if not self.can_assess(request.user):
            raise exceptions.ProposalNotAuthorized()

        if self.processing_status != status:
            if self.processing_status == Proposal.PROCESSING_STATUS_WITH_APPROVER:
                self.approver_comment = ''
                if approver_comment:
                    self.approver_comment = approver_comment
                    self.save()
                    send_proposal_approver_sendback_email_notification(request, self)
            self.processing_status = status
            self.save()
            logger.info(f'Status:[{status}] has been set to the proposal: [{self}]')
            if status == self.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS:
                self.add_default_requirements()

            # Create a log entry for the proposal
            if self.processing_status == self.PROCESSING_STATUS_WITH_ASSESSOR:
                self.log_user_action(ProposalUserAction.ACTION_BACK_TO_PROCESSING.format(self.lodgement_number), request)
            elif self.processing_status == self.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS:
                self.log_user_action(ProposalUserAction.ACTION_ENTER_REQUIREMENTS.format(self.lodgement_number), request)

    def reissue_approval(self, request):
        with transaction.atomic():
            vessels = []
            if type(self.child_obj) == MooringLicenceApplication:
                # vessels.append([vo.vessel for vo in self.listed_vessels.all()])
                vessels.extend([vo.vessel for vo in self.listed_vessels.all()])
            else:
                vessels.append(self.vessel_details.vessel)
            # Non MLA
            proposals = [proposal for proposal in Proposal.objects.filter(vessel_details__vessel__in=vessels).
                    exclude(id=self.id).
                    exclude(processing_status__in=['discarded', 'sticker_to_be_returned', 'printing_sticker', 'approved', 'declined'])
                        ]
            # MLA
            proposals.extend([proposal for proposal in Proposal.objects.
                filter(listed_vessels__end_date__isnull=True).
                filter(listed_vessels__vessel__in=vessels).
                exclude(id=self.id).
                exclude(processing_status__in=['discarded', 'sticker_to_be_returned', 'printing_sticker', 'approved', 'declined'])
                ])

            if not self.processing_status == Proposal.PROCESSING_STATUS_APPROVED:
                raise ValidationError('You cannot change the current status at this time')
            elif proposals:
                raise ValidationError('Error message: there is an application in status other than (Discarded, Sticker To Be Returned, Printing Sticker, Approved, or Declined)')
            elif self.approval and self.approval.can_reissue and self.is_approver(request.user):
                # update vessel details
                vessel_details = self.vessel_details.vessel.latest_vessel_details
                self.vessel_type = vessel_details.vessel_type if vessel_details else ''
                self.vessel_name = vessel_details.vessel_name if vessel_details else ''
                self.vessel_length = vessel_details.vessel_length if vessel_details else 0.00
                self.vessel_draft = vessel_details.vessel_draft if vessel_details else 0.00
                self.vessel_beam = vessel_details.vessel_beam if vessel_details else 0.00
                self.vessel_weight = vessel_details.vessel_weight if vessel_details else 0.00
                self.berth_mooring = vessel_details.berth_mooring if vessel_details else ''

                self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
                self.proposed_issuance_approval = {}
                self.save()
                self.approval.reissued=True
                self.approval.save()
                # Create a log entry for the proposal
                self.log_user_action(ProposalUserAction.ACTION_REISSUE_APPROVAL.format(self.lodgement_number), request)
            else:
                raise ValidationError('Cannot reissue Approval')

    def proposed_decline(self,request,details):
        with transaction.atomic():
            logger.info(f'Processing proposed decline... for the Proposal: [{self}]')
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if self.processing_status != Proposal.PROCESSING_STATUS_WITH_ASSESSOR:
                    raise ValidationError('You cannot propose to decline if it is not with assessor')

                reason = details.get('reason', '')
                ProposalDeclinedDetails.objects.update_or_create(
                    proposal=self,
                    defaults={
                        'officer': request.user.id,
                        'reason': reason,
                        'cc_email': details.get('cc_email', None)
                    }
                )
                self.proposed_decline_status = True
                approver_comment = ''
                self.move_to_status(request, Proposal.PROCESSING_STATUS_WITH_APPROVER, approver_comment)
                # Log proposal action
                self.log_user_action(ProposalUserAction.ACTION_PROPOSED_DECLINE.format(self.lodgement_number), request)
                # Log entry for organisation
                applicant_field = getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_PROPOSED_DECLINE.format(self.id), request)

                send_approver_approve_decline_email_notification(request, self)
            except:
                raise

    def final_decline(self, request, details):
        from mooringlicensing.components.approvals.models import WaitingListAllocation

        with transaction.atomic():
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()

                if self.application_type.code in (WaitingListApplication.code, AnnualAdmissionApplication.code):
                    if self.processing_status not in (Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS, Proposal.PROCESSING_STATUS_WITH_ASSESSOR):
                        # For WLA or AAA, assessor can final decline
                        raise ValidationError('You cannot decline if it is not with approver')
                else:
                    if self.processing_status != Proposal.PROCESSING_STATUS_WITH_APPROVER:
                        # For AuA or MLA, approver can final decline
                        raise ValidationError('You cannot decline if it is not with approver')

                proposal_decline, success = ProposalDeclinedDetails.objects.update_or_create(
                    proposal=self,
                    defaults={
                        'officer': request.user.id,
                        'reason': details.get('reason', ''),
                        'cc_email': details.get('cc_email',None)
                    }
                )
                self.proposed_decline_status = True
                self.processing_status = Proposal.PROCESSING_STATUS_DECLINED
                self.save()
                # Log proposal action
                self.log_user_action(ProposalUserAction.ACTION_DECLINE.format(self.id),request)
                # Log entry for organisation
                applicant_field=getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_DECLINE.format(self.id),request)
                # update WLA internal_status

                ## ML
                if type(self.child_obj) == MooringLicenceApplication and self.waiting_list_allocation:
                    # Originated WLAllocation should gets the status 'waiting' again.
                    self.waiting_list_allocation.internal_status = WaitingListAllocation.INTERNAL_STATUS_WAITING
                    self.waiting_list_allocation.save()
                    logger.info(f'Internal status: [{WaitingListAllocation.INTERNAL_STATUS_WAITING}] has been set to the WLAllocation: [{self.waiting_list_allocation}.]')
                    self.waiting_list_allocation.set_wla_order()
                send_application_approved_or_declined_email(self, 'declined', request)
            except:
                raise

    def on_hold(self,request):
        with transaction.atomic():
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if not (self.processing_status == 'with_assessor' or self.processing_status == 'with_referral'):
                    raise ValidationError('You cannot put on hold if it is not with assessor or with referral')

                self.prev_processing_status = self.processing_status
                self.processing_status = self.PROCESSING_STATUS_ONHOLD
                self.save()
                # Log proposal action
                self.log_user_action(ProposalUserAction.ACTION_PUT_ONHOLD.format(self.id),request)
                # Log entry for organisation
                applicant_field=getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_PUT_ONHOLD.format(self.id),request)
            except:
                raise

    def on_hold_remove(self,request):
        with transaction.atomic():
            try:
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if self.processing_status != 'on_hold':
                    raise ValidationError('You cannot remove on hold if it is not currently on hold')

                self.processing_status = self.prev_processing_status
                self.prev_processing_status = self.PROCESSING_STATUS_ONHOLD
                self.save()
                # Log proposal action
                self.log_user_action(ProposalUserAction.ACTION_REMOVE_ONHOLD.format(self.id),request)
                # Log entry for organisation
                applicant_field=getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_REMOVE_ONHOLD.format(self.id),request)
            except:
                raise

    def proposed_approval(self, request, details):
        with transaction.atomic():
            try:
                logger.info(f'Processing proposed approval... for the Proposal: [{self}]')
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if self.processing_status != Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS:
                    raise ValidationError('You cannot propose for approval if it is not with assessor for requirements')

                current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
                current_date = current_datetime.date()

                ria_mooring_name = ''
                mooring_id = details.get('mooring_id')
                if mooring_id:
                    ria_mooring_name = Mooring.objects.get(id=mooring_id).name
                self.proposed_issuance_approval = {
                    'current_date': current_date.strftime('%d/%m/%Y'),  # start_date and expiry_date are determined when making payment or approved???
                    'mooring_bay_id': details.get('mooring_bay_id'),
                    'mooring_id': mooring_id,
                    'ria_mooring_name': ria_mooring_name,
                    'details': details.get('details'),
                    'cc_email': details.get('cc_email'),
                    'mooring_on_approval': details.get('mooring_on_approval'),
                    'vessel_ownership': details.get('vessel_ownership'),
                }
                self.proposed_decline_status = False
                approver_comment = ''
                self.move_to_status(request, Proposal.PROCESSING_STATUS_WITH_APPROVER, approver_comment)
                self.assigned_officer = None
                self.save()
                # Log proposal action
                self.log_user_action(ProposalUserAction.ACTION_PROPOSED_APPROVAL.format(self.lodgement_number), request)
                # Log entry for organisation
                applicant_field = getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_PROPOSED_APPROVAL.format(self.id), request)

                send_approver_approve_decline_email_notification(request, self)
                return self

            except:
                raise

    def preview_approval(self,request,details):
        from mooringlicensing.components.approvals.models import PreviewTempApproval
        with transaction.atomic():
            try:
                if self.processing_status != 'with_approver':
                    raise ValidationError('Licence preview only available when processing status is with_approver. Current status {}'.format(self.processing_status))
                if not self.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if not self.applicant_address:
                    raise ValidationError('The applicant needs to have set their postal address before approving this proposal.')

                lodgement_number = self.previous_application.approval.lodgement_number if self.proposal_type in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_SWAP_MOORINGS,] else None # renewals/amendments keep same licence number
                preview_approval = PreviewTempApproval.objects.create(
                    current_proposal = self,
                    issue_date = timezone.now(),
                    expiry_date = datetime.datetime.strptime(details.get('due_date'), '%d/%m/%Y').date(),
                    start_date = datetime.datetime.strptime(details.get('start_date'), '%d/%m/%Y').date(),
                    submitter = self.submitter,
                    org_applicant = self.org_applicant,
                    proxy_applicant = self.proxy_applicant,
                    lodgement_number = lodgement_number
                )

                # Generate the preview document - get the value of the BytesIO buffer
                licence_buffer = preview_approval.generate_doc(preview=True)

                # clean temp preview licence object
                transaction.set_rollback(True)

                return licence_buffer
            except:
                raise

    def final_approval_for_WLA_AAA(self, request, details=None):
        with transaction.atomic():
            try:
                logger.info(f'Processing final_approval...for the proposal: [{self}].')

                current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
                self.proposed_decline_status = False

                # Validation & update proposed_issuance_approval
                if (self.processing_status == Proposal.PROCESSING_STATUS_AWAITING_PAYMENT and self.fee_paid) or self.proposal_type == PROPOSAL_TYPE_AMENDMENT:
                    # for 'Awaiting Payment' approval. External/Internal user fires this method after full payment via Make/Record Payment
                    pass
                else:
                    if not self.auto_approve and not self.can_assess(request.user):
                        raise exceptions.ProposalNotAuthorized()
                    if not self.auto_approve and self.processing_status not in (Proposal.PROCESSING_STATUS_WITH_ASSESSOR_REQUIREMENTS, Proposal.PROCESSING_STATUS_WITH_ASSESSOR):
                        raise ValidationError('You cannot issue the approval if it is not with an assessor')
                    if not self.applicant_address:
                        raise ValidationError('The applicant needs to have set their postal address before approving this proposal.')

                    if self.application_fees.count() < 1 and not self.migrated:
                        raise ValidationError('Payment record not found for the Annual Admission Application: {}'.format(self))
                    # elif self.application_fees.count() > 1:
                    #     raise ValidationError('More than 1 payment records found for the Annual Admission Application: {}'.format(self))

                    if details:
                        # When auto_approve, there are no 'details' because details are created from the modal when assessment
                        self.proposed_issuance_approval = {
                            'details': details.get('details'),
                            'cc_email': details.get('cc_email'),
                        }
                    else:
                        self.proposed_issuance_approval = {}
                self.save()

                # Create/update approval
                created = None
                if self.proposal_type in (ProposalType.objects.filter(code__in=(PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT))):
                    approval = self.approval.child_obj
                    approval.current_proposal=self
                    approval.issue_date = current_datetime
                    approval.start_date = current_datetime.date()
                    approval.expiry_date = self.end_date
                    approval.submitter = self.submitter
                    approval.save()
                else:
                    approval, created = self.approval_class.objects.update_or_create(
                        current_proposal=self,
                        defaults={
                            'issue_date': current_datetime,
                            'start_date': current_datetime.date(),
                            'expiry_date': self.end_date,
                            'submitter': self.submitter,
                        }
                    )
                    if created:
                        logger.info(f'New approval: [{approval}] has been created.')
                        approval.log_user_action(f'New approval: {approval} has been created.', request)
                self.approval = approval
                self.save()

                # always reset this flag
                approval.renewal_sent = False
                if type(self.child_obj) == AnnualAdmissionApplication:
                    approval.export_to_mooring_booking = True
                approval.save()
                # set auto_approve ProposalRequirement due dates to those from previous application + 12 months
                if self.auto_approve and self.proposal_type.code == 'renewal':
                    for req in self.requirements.filter(is_deleted=False):
                        if req.copied_from and req.copied_from.due_date:
                            req.due_date = req.copied_from.due_date + relativedelta(months=+12)
                            req.save()

                # Generate compliances
                from mooringlicensing.components.compliances.models import Compliance, ComplianceUserAction
                #target_proposal = self.previous_application if self.previous_application else self
                target_proposal = self.previous_application if self.proposal_type.code == 'amendment' else self
                for compliance in Compliance.objects.filter(
                    approval=approval.approval,
                    proposal=target_proposal,
                    processing_status='future',
                    ):
                    compliance.processing_status='discarded'
                    compliance.customer_status = 'discarded'
                    compliance.reminder_sent=True
                    compliance.post_reminder_sent=True
                    compliance.save()
                #approval_compliances.delete()
                self.generate_compliances(approval, request)

                # Log entry for organisation
                applicant_field = getattr(self, self.applicant_field)
                # Log proposal action
                if details:
                    # When not auto-approve
                    self.log_user_action(ProposalUserAction.ACTION_APPROVED.format(self.lodgement_number), request)
                    # applicant_field.log_user_action(ProposalUserAction.ACTION_APPROVED.format(self.id), request)
                else:
                    # When auto approve
                    self.log_user_action(ProposalUserAction.ACTION_AUTO_APPROVED.format(self.lodgement_number),)
                    # applicant_field.log_user_action(ProposalUserAction.ACTION_AUTO_APPROVED.format(self.id),)

                # set proposal status to approved - can change later after manage_stickers
                self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
                self.save()

                # Update stickers
                new_sticker, sticker_to_be_returned = self.approval.manage_stickers(self)

                # Handle this proposal status
                if self.approval and self.approval.reissued:
                    # Can only change the conditions, so goes to Approved
                    self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
                else:
                    if sticker_to_be_returned:
                        self.processing_status = Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED
                    elif new_sticker:
                        self.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
                self.save()

                from mooringlicensing.components.approvals.models import Sticker

                # set wla order
                from mooringlicensing.components.approvals.models import WaitingListAllocation
                if (type(approval) == WaitingListAllocation and 
                        (self.proposal_type.code == PROPOSAL_TYPE_NEW or 
                            (self.previous_application.preferred_bay != self.preferred_bay)
                            )
                        ):
                    from mooringlicensing.components.approvals.models import Approval
                    approval.internal_status = Approval.INTERNAL_STATUS_WAITING
                    approval.wla_queue_date = current_datetime
                    approval.save()
                    approval = approval.set_wla_order()

                # send Proposal approval email with attachment
                approval.generate_doc()
                send_application_approved_or_declined_email(self, 'approved', request, [sticker_to_be_returned,])
                self.save(version_comment='Final Approval: {}'.format(self.approval.lodgement_number))
                self.approval.approval_documents.all().update(can_delete=False)

                # write approval history
                if self.approval and self.approval.reissued:
                    approval.write_approval_history('Reissue via application {}'.format(self.lodgement_number))
                elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_RENEWAL):
                    approval.write_approval_history('Renewal application {}'.format(self.lodgement_number))
                elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_AMENDMENT):
                    approval.write_approval_history('Amendment application {}'.format(self.lodgement_number))
                else:
                    approval.write_approval_history()

                # Reset flag
                if self.approval:
                    self.approval.reissued = False
                    self.approval.save()

                return self

            except Exception as e:
                raise

    def refresh(self):
        self.approval.refresh_from_db()
        self.refresh_from_db()
        self.child_obj.refresh_from_db()

    def final_approval_for_AUA_MLA(self, request=None, details=None):
        with transaction.atomic():
            try:
                logger.info(f'Processing final_approval...for the proposal: [{self}].')

                self.proposed_decline_status = False

                # Validation & update proposed_issuance_approval
                if (self.processing_status == Proposal.PROCESSING_STATUS_AWAITING_PAYMENT and self.fee_paid) or self.proposal_type == PROPOSAL_TYPE_AMENDMENT:
                    # TODO: rework this
                    # for 'Awaiting Payment' approval. External/Internal user fires this method after full payment via Make/Record Payment
                    pass
                else:
                    if request and not self.can_assess(request.user):
                        raise exceptions.ProposalNotAuthorized()
                    if request and self.processing_status not in (Proposal.PROCESSING_STATUS_WITH_APPROVER,):
                        raise ValidationError('You cannot issue the approval if it is not with an assessor')
                    if not self.applicant_address:
                        raise ValidationError('The applicant needs to have set their postal address before approving this proposal.')

                ## update proposed_issuance_approval
                if details:
                    ria_mooring_name = ''
                    mooring_id = details.get('mooring_id')
                    if mooring_id:
                        ria_mooring_name = Mooring.objects.get(id=mooring_id).name

                    self.proposed_issuance_approval = {
                        'mooring_bay_id': details.get('mooring_bay_id'),
                        'mooring_id': mooring_id,
                        'ria_mooring_name': ria_mooring_name,
                        'details': details.get('details'),
                        'cc_email': details.get('cc_email'),
                        'mooring_on_approval': details.get('mooring_on_approval'),
                        'vessel_ownership': details.get('vessel_ownership'),
                    }
                    self.save()

                # if no request, must be a system reissue - skip payment section
                # when reissuing, no new invoices should be created
                if not request or (request and self.approval and self.approval.reissued):
                    # system reissue or admin reissue
                    approval, created = self.child_obj.update_or_create_approval(datetime.datetime.now(pytz.timezone(TIME_ZONE)), request)

                    #--- Reflect any changes made in the function above (update_or_create_approval) ---#
                    # self.approval.refresh_from_db()
                    # self.refresh_from_db()
                    # self.child_obj.refresh_from_db()
                    self.refresh()
                    #-------------------------------#

                    self.approval = approval.approval
                    self.save()
                else:
                    ## prepare invoice
                    # from mooringlicensing.components.payments_ml.utils import create_fee_lines, make_serializable
                    from mooringlicensing.components.payments_ml.models import FeeConstructor, ApplicationFee

                    # create fee lines tells us whether a payment is required
                    line_items, fee_items_to_store = self.child_obj.create_fee_lines()  # Accessed by AU and ML

                    total_amount = sum(line_item['price_incl_tax'] for line_item in line_items)

                    if total_amount == 0:
                        # Call a function where mooringonapprovals and stickers are handled, because when total_amount == 0,
                        # Ledger skips the payment step, which calling the function below
                        approval, created = self.child_obj.update_or_create_approval(datetime.datetime.now(pytz.timezone(TIME_ZONE)), request=request)

                        #--- Reflect any changes made in the function above (update_or_create_approval) ---#
                        if self.approval:
                            self.approval.refresh_from_db()
                        self.refresh_from_db()  # Reflect child_ojb's attributes, such as processing_status, to this proposal object.
                        self.child_obj.refresh_from_db()
                        #-------------------------------#
                    else:
                        # proposal type must be awaiting payment
                        self.processing_status = Proposal.PROCESSING_STATUS_AWAITING_PAYMENT
                        self.save()
                        logger.info(f'Status: [{Proposal.PROCESSING_STATUS_AWAITING_PAYMENT}] has been set to the proposal: [{self}]')

                        from mooringlicensing.components.payments_ml.models import FeeItem
                        from mooringlicensing.components.payments_ml.models import FeeItemApplicationFee

                        try:
                            logger.info(f'Creating future invoice for the application: [{self}]...')

                            # Following two lines are for future invoicing.
                            # However because we need to segregate 'ledger' from this system, we cannot use these two functions.
                            # TODO: Review and rewrite to create an invoice with ledger_client_api.
                            # basket = createCustomBasket(line_items, self.submitter, PAYMENT_SYSTEM_ID)
                            # order = CreateInvoiceBasket(payment_method='other', system=PAYMENT_SYSTEM_PREFIX).create_invoice_and_order(basket, 0, None, None, user=self.submitter, invoice_text='Payment Invoice')
                            # invoice = Invoice.objects.get(order_number=order.number)

                            ### Future Invoice ###
                            reference = self.previous_application.lodgement_number if self.previous_application else self.lodgement_number
                            invoice_text = 'Payment Invoice'
                            basket_params = {
                                'products': line_items,
                                'vouchers': [],
                                'system': settings.PAYMENT_SYSTEM_ID,
                                'custom_basket': True,
                                'booking_reference': reference,
                                'no_payment': True,
                                'tax_override': True,
                            }
                            logger.info(f'basket_params: {basket_params}')

                            from ledger_api_client.utils import create_basket_session, process_create_future_invoice
                            basket_hash = create_basket_session(request, self.submitter, basket_params)

                            application_fee = ApplicationFee.objects.create(
                                proposal=self,
                                # invoice_reference=invoice.reference,
                                payment_type=ApplicationFee.PAYMENT_TYPE_TEMPORARY,
                            )
                            return_preload_url = settings.MOORING_LICENSING_EXTERNAL_URL + reverse("ledger-api-success-callback", kwargs={"uuid": application_fee.uuid})

                            basket_hash_split = basket_hash.split("|")
                            pcfi = process_create_future_invoice(basket_hash_split[0], invoice_text, return_preload_url)

                            application_fee.invoice_reference = pcfi['data']['invoice']
                            application_fee.save()
                            logger.info(f'ApplicationFee: [{application_fee}] has been created for the proposal: [{self}].')
                            ### END: Future Invoice ###

                            # Link between ApplicationFee and FeeItem(s)
                            for item in fee_items_to_store:
                                fee_item = FeeItem.objects.get(id=item['fee_item_id'])
                                vessel_details_id = item['vessel_details_id']  # This could be '' when null vessel application
                                vessel_details = VesselDetails.objects.get(id=vessel_details_id) if vessel_details_id else None
                                amount_to_be_paid = item['fee_amount_adjusted']
                                fiaf = FeeItemApplicationFee.objects.create(
                                    fee_item=fee_item,
                                    application_fee=application_fee,
                                    vessel_details=vessel_details,
                                    amount_to_be_paid=amount_to_be_paid,
                                )
                                logger.info(f'FeeItemApplicationFee: [{fiaf}] has been created.')

                            if not self.payment_required():
                                self.approval.generate_doc()
                            send_application_approved_or_declined_email(self, 'approved', request)
                            self.log_user_action(ProposalUserAction.ACTION_APPROVE_APPLICATION.format(self.lodgement_number), request)

                        except Exception as e:
                            err_msg = 'Failed to create invoice'
                            logger.error('{}\n{}'.format(err_msg, str(e)))

                # Reset flag
                if self.approval:
                    self.approval.reissued = False
                    self.approval.save()

                return self
            except Exception as e:
                print(e)
                msg = 'final_approval_for_AUA_MLA. lodgement number {}, error: {}'.format(self.lodgement_number, str(e))
                logger.error(msg)
                logger.error(traceback.print_exc())
                raise e

    def final_approval(self, request=None, details=None):
        if self.child_obj.code in (WaitingListApplication.code, AnnualAdmissionApplication.code):
            self.final_approval_for_WLA_AAA(request, details)
        elif self.child_obj.code in (AuthorisedUserApplication.code, MooringLicenceApplication.code):
            return self.final_approval_for_AUA_MLA(request, details)

    def generate_compliances(self,approval, request):
        today = timezone.now().date()
        timedelta = datetime.timedelta
        from mooringlicensing.components.compliances.models import Compliance, ComplianceUserAction
        if self.previous_application:
            try:
                for r in self.requirements.filter(copied_from__isnull=False):
                    cs=[]
                    cs=Compliance.objects.filter(requirement=r.copied_from, proposal=self.previous_application, processing_status__in=['due','approved'])
                    if cs:
                        if r.is_deleted == True:
                            for c in cs:
                                c.processing_status='discarded'
                                c.customer_status = 'discarded'
                                c.reminder_sent=True
                                c.post_reminder_sent=True
                                c.save()
                        if r.is_deleted == False:
                            for c in cs:
                                c.proposal= self
                                c.approval=approval
                                c.requirement=r
                                c.save()
            except:
                raise
        requirement_set= self.requirements.all().exclude(is_deleted=True)

        for req in requirement_set:
            try:
                if req.due_date and req.due_date >= today:
                    current_date = req.due_date
                    #create a first Compliance
                    try:
                        compliance= Compliance.objects.get(requirement = req, due_date = current_date)
                    except Compliance.DoesNotExist:
                        compliance =Compliance.objects.create(
                                    proposal=self,
                                    due_date=current_date,
                                    processing_status='future',
                                    customer_status='future',
                                    approval=approval,
                                    requirement=req,
                        )
                        compliance.log_user_action(ComplianceUserAction.ACTION_CREATE.format(compliance.id),request)
                    if req.recurrence:
                        while current_date < approval.expiry_date:
                            for x in range(req.recurrence_schedule):
                            #Weekly
                                if req.recurrence_pattern == 1:
                                    #current_date += timedelta(weeks=1)
                                    current_date += relativedelta(weeks=+1)
                            #Monthly
                                elif req.recurrence_pattern == 2:
                                    #current_date += timedelta(weeks=4)
                                    current_date += relativedelta(months=+1)
                                    pass
                            #Yearly
                                elif req.recurrence_pattern == 3:
                                    #current_date += timedelta(days=365)
                                    current_date += relativedelta(years=+1)
                            # Create the compliance
                            if current_date <= approval.expiry_date:
                                try:
                                    compliance= Compliance.objects.get(requirement = req, due_date = current_date)
                                except Compliance.DoesNotExist:
                                    compliance =Compliance.objects.create(
                                                proposal=self,
                                                due_date=current_date,
                                                processing_status='future',
                                                customer_status='future',
                                                approval=approval,
                                                requirement=req,
                                    )
                                    compliance.log_user_action(ComplianceUserAction.ACTION_CREATE.format(compliance.id),request)
            except:
                raise

    def add_vessels_and_moorings_from_licence(self):
        if self.approval and type(self) is MooringLicenceApplication:
            for vooa in self.approval.vesselownershiponapproval_set.filter(
                    Q(end_date__isnull=True) &
                    Q(vessel_ownership__end_date__isnull=True)
                    ):
                self.listed_vessels.add(vooa.vessel_ownership)
        elif self.approval and type(self) is AuthorisedUserApplication:
            for moa in self.approval.mooringonapproval_set.filter(
                    Q(end_date__isnull=True)
                    ):
                self.listed_moorings.add(moa.mooring)
        self.save()

    def clone_proposal_with_status_reset(self):
        with transaction.atomic():
            try:
                proposal = type(self.child_obj).objects.create()
                proposal.processing_status = Proposal.PROCESSING_STATUS_DRAFT
                proposal.previous_application = self
                proposal.approval = self.approval
                proposal.mooring_authorisation_preference = self.mooring_authorisation_preference
                proposal.null_vessel_on_create = not self.vessel_on_proposal()

                logger.info(f'Cloning the proposal: [{self}] to the proposal: [{proposal}]...')

                self.proposal_applicant.copy_self_to_proposal(proposal)

                proposal.save(no_revision=True)
                return proposal
            except:
                raise

    @property
    def proposal_applicant(self):
        proposal_applicant = ProposalApplicant.objects.filter(proposal=self).order_by('updated_at').last()
        return proposal_applicant

    def renew_approval(self,request):
        with transaction.atomic():
            previous_proposal = self
            try:
                logger.info(f'Renewing approval: [{self.approval}]...')

                # proposal = clone_proposal_with_status_reset(self)
                proposal = self.clone_proposal_with_status_reset()
                proposal.proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_RENEWAL)
                proposal.submitter = request.user.id
                proposal.previous_application = self
                proposal.proposed_issuance_approval= None

                from mooringlicensing.components.approvals.models import MooringLicence
                if self.approval.child_obj.code == MooringLicence.code:
                    proposal.allocated_mooring = self.approval.child_obj.mooring

                    # Copy links to the documents so that the documents are shown on the amendment application form
                    self.copy_proof_of_identity_documents(proposal)
                    self.copy_mooring_report_documents(proposal)
                    self.copy_written_proof_documents(proposal)
                    self.copy_signed_licence_agreement_documents(proposal)

                req=self.requirements.all().exclude(is_deleted=True)
                from copy import deepcopy
                if req:
                    for r in req:
                        old_r = deepcopy(r)
                        r.proposal = proposal
                        r.copied_from=old_r
                        r.copied_for_renewal=True
                        if r.due_date:
                            r.due_date=None
                            r.require_due_date=True
                        r.id = None
                        r.district_proposal=None
                        r.save()

                #Log entry for approval
                from mooringlicensing.components.approvals.models import ApprovalUserAction
                self.approval.log_user_action(ApprovalUserAction.ACTION_RENEW_APPROVAL.format(self.approval.id),request)
                proposal.save(version_comment='New Amendment/Renewal Application created, from origin {}'.format(proposal.previous_application_id))
                proposal.add_vessels_and_moorings_from_licence()
                return proposal
            except Exception as e:
                raise e

    def amend_approval(self,request):
        with transaction.atomic():
            previous_proposal = self
            try:
                logger.info(f'Amending approval: [{self.approval}]...')

                add_vessel = request.data.get('add_vessel', False)  # This value comes from the radio button implemented on the proposal_apply.vue

                # proposal = clone_proposal_with_status_reset(self)
                proposal = self.clone_proposal_with_status_reset()
                proposal.proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_AMENDMENT)
                proposal.submitter = request.user.id
                proposal.previous_application = self
                proposal.keep_existing_vessel = not add_vessel

                from mooringlicensing.components.approvals.models import MooringLicence
                if self.approval.child_obj.code == MooringLicence.code:
                    proposal.allocated_mooring = self.approval.child_obj.mooring

                    # Copy links to the documents so that the documents are shown on the amendment application form
                    self.copy_proof_of_identity_documents(proposal)
                    self.copy_mooring_report_documents(proposal)
                    self.copy_written_proof_documents(proposal)
                    self.copy_signed_licence_agreement_documents(proposal)

                req=self.requirements.all().exclude(is_deleted=True)
                from copy import deepcopy
                if req:
                    for r in req:
                        old_r = deepcopy(r)
                        r.proposal = proposal
                        r.copied_from=old_r
                        r.id = None
                        r.district_proposal=None
                        r.save()

                # Create a log entry for the proposal
                self.log_user_action(ProposalUserAction.ACTION_AMEND_PROPOSAL.format(self.id),request)

                # Create a log entry for the organisation
                # applicant_field=getattr(self, self.applicant_field)
                # applicant_field.log_user_action(ProposalUserAction.ACTION_AMEND_PROPOSAL.format(self.id),request)

                #Log entry for approval
                from mooringlicensing.components.approvals.models import ApprovalUserAction
                self.approval.log_user_action(ApprovalUserAction.ACTION_AMEND_APPROVAL.format(self.approval.id),request)

                proposal.save(version_comment='New Amendment/Renewal Application created, from origin {}'.format(proposal.previous_application_id))
                proposal.add_vessels_and_moorings_from_licence()
                return proposal
            except Exception as e:
                raise e

    @property
    def application_type(self):
        application_type = ApplicationType.objects.get(code=self.application_type_code)
        return application_type

    # tmp property required to fix dev data
    @property
    def no_child_obj(self):
        no_child_obj = True
        if hasattr(self, 'waitinglistapplication'):
            no_child_obj = False
        elif hasattr(self, 'annualadmissionapplication'):
            no_child_obj = False
        elif hasattr(self, 'authoriseduserapplication'):
            no_child_obj = False
        elif hasattr(self, 'mooringlicenceapplication'):
            no_child_obj = False
        return no_child_obj

    @property
    def child_obj(self):
        if hasattr(self, 'waitinglistapplication'):
            return self.waitinglistapplication
        elif hasattr(self, 'annualadmissionapplication'):
            return self.annualadmissionapplication
        elif hasattr(self, 'authoriseduserapplication'):
            return self.authoriseduserapplication
        elif hasattr(self, 'mooringlicenceapplication'):
            return self.mooringlicenceapplication
        else:
            raise ObjectDoesNotExist("Proposal must have an associated child object - WLA, AA, AU or ML")

    @property
    def previous_application_notnull_vessel(self):
        prev_application = self.previous_application
        while prev_application and not prev_application.vessel_details:
            prev_application = prev_application.previous_application
        return prev_application

    @property
    def previous_application_status_filter(self):
        prev_application = self.previous_application
        while prev_application and prev_application.processing_status in [Proposal.PROCESSING_STATUS_DISCARDED, Proposal.PROCESSING_STATUS_DECLINED,] and prev_application.previous_application:
            prev_application = prev_application.previous_application
        return prev_application

    @property
    def approval_class(self):
        from mooringlicensing.components.approvals.models import WaitingListAllocation, AnnualAdmissionPermit, AuthorisedUserPermit, MooringLicence
        if hasattr(self, 'waitinglistapplication'):
            return WaitingListAllocation
        elif hasattr(self, 'annualadmissionapplication'):
            return AnnualAdmissionPermit
        elif hasattr(self, 'authoriseduserapplication'):
            return AuthorisedUserPermit
        elif hasattr(self, 'mooringlicenceapplication'):
            return MooringLicence
        else:
            raise ObjectDoesNotExist("Proposal must have an associated child object - WLA, AA, AU or ML")

    @property
    def application_type_code(self):
        if type(self) == Proposal:
            return self.child_obj.code
        else:
            return self.code

    @property
    def description(self):
        return self.child_obj.description

    @classmethod
    def application_type_descriptions(cls):
        type_list = []
        for application_type in Proposal.__subclasses__():
            type_list.append(application_type.description)
        return type_list

    @classmethod
    def application_types_dict(cls, apply_page):
        type_list = []
        for application_type in Proposal.__subclasses__():
            if apply_page:
                if application_type.apply_page_visibility:
                    type_list.append({
                        "code": application_type.code,
                        "description": application_type.description,
                        "new_application_text": application_type.new_application_text
                        })
            else:
                type_list.append({
                    "code": application_type.code,
                    "description": application_type.description,
                    "new_application_text": application_type.new_application_text
                })

        return type_list

    @classmethod
    def application_categories_dict(cls, apply_page):
        category_list = []
        for category in settings.PROPOSAL_TYPES:
            category_list.append({
                "code": category['code'],
                "description": category['description'],
            })
        return category_list

    def get_target_date(self, applied_date):
        logger.info(f'Proposal.get_target_date() is called with the parameter applied_date: {applied_date}')

        if self.proposal_type.code == settings.PROPOSAL_TYPE_AMENDMENT:
            if applied_date < self.approval.latest_applied_season.start_date:
                # This amendment application is being applied after the renewal but before the new season starts
                # Set the target date used for calculation to the 1st date of the latest season applied
                target_date = self.approval.latest_applied_season.start_date
            elif self.approval.latest_applied_season.start_date <= applied_date <= self.approval.latest_applied_season.end_date:
                # This amendment application is being applied during the latest season applied to the approval
                # This is the most likely case
                target_date = applied_date
            else:
                msg = 'Approval: {} cannot be amended before renewal'.format(self.approval)
                logger.error(msg)
                raise Exception(msg)
        elif self.proposal_type.code == settings.PROPOSAL_TYPE_RENEWAL:
            if applied_date < self.approval.latest_applied_season.start_date:  # This should be same as self.approval.expiry_date
                # This renewal is being applied before the latest season starts
                # Therefore this application is renewal application reissued.
                target_date = self.approval.latest_applied_season.start_date
                # msg = 'Approval: {} has been probably renewed, already'.format(self.approval)
                # logger.error(msg)
                # raise Exception(msg)
            elif self.approval.latest_applied_season.start_date <= applied_date <= self.approval.latest_applied_season.end_date:
                # This renewal application is being applied before the licence expiry
                # This is the most likely case
                # Set the target_date to the 1st day of the next season
                target_date = self.approval.latest_applied_season.end_date + datetime.timedelta(days=1)
            else:
                # Renewal application is being applied after the approval expiry date... Not sure if this is allowed.
                target_date = applied_date
        elif self.proposal_type.code in [settings.PROPOSAL_TYPE_NEW, settings.PROPOSAL_TYPE_SWAP_MOORINGS,]:
            target_date = applied_date
        else:
            raise ValueError('Unknown proposal type of the proposal: {}'.format(self))

        return target_date

    #def auto_approve_check(self, request):
    #    # New AnnualAdmission can be auto_approved
    #    if type(self.child_obj) == AnnualAdmissionApplication and self.proposal_type.code == 'new':
    #        self.auto_approve = True
    #        self.save()
    #    ## WLA
    #    if (type(self.child_obj) == WaitingListApplication and 
    #            self.previous_application_status_filter and 
    #            self.preferred_bay != self.previous_application_status_filter.preferred_bay
    #            ):
    #        self.auto_approve = False
    #        self.save()

    #    if self.auto_approve:
    #        self.final_approval_for_WLA_AAA(request, details={})

    def vessel_on_proposal(self):
        from mooringlicensing.components.approvals.models import MooringLicence
        # Test to see if vessel should be read in from submitted data
        vessel_exists = False
        if self.approval and type(self.approval) is not MooringLicence:
            vessel_exists = (True if
                    self.approval and self.approval.current_proposal and 
                    self.approval.current_proposal.vessel_details and
                    not self.approval.current_proposal.vessel_ownership.end_date
                    else False)
        else:
            vessel_exists = True if self.listed_vessels.filter(end_date__isnull=True) else False
        return vessel_exists

    def validate_vessel_length(self, request):
        self.child_obj.validate_vessel_length(request)

    def validate_against_existing_proposals_and_approvals(self):
        self.child_obj.validate_against_existing_proposals_and_approvals()


class ProposalApplicant(RevisionedMixin):
    proposal = models.ForeignKey(Proposal, null=True, blank=True, on_delete=models.SET_NULL)

    # Name, etc
    first_name = models.CharField(max_length=128, blank=True, verbose_name='Given name(s)')
    last_name = models.CharField(max_length=128, blank=True)
    dob = models.DateField(auto_now=False, auto_now_add=False, null=True, blank=True, verbose_name="date of birth", help_text='')

    # Residential address
    residential_line1 = models.CharField('Line 1', max_length=255, blank=True)
    residential_line2 = models.CharField('Line 2', max_length=255, blank=True)
    residential_line3 = models.CharField('Line 3', max_length=255, blank=True)
    residential_locality = models.CharField('Suburb / Town', max_length=255, blank=True)
    residential_state = models.CharField(max_length=255, default='WA', blank=True)
    residential_country = CountryField(default='AU', blank=True)
    residential_postcode = models.CharField(max_length=10, blank=True)

    # Postal address
    postal_same_as_residential = models.NullBooleanField(default=False)
    postal_line1 = models.CharField('Line 1', max_length=255, blank=True)
    postal_line2 = models.CharField('Line 2', max_length=255, blank=True)
    postal_line3 = models.CharField('Line 3', max_length=255, blank=True)
    postal_locality = models.CharField('Suburb / Town', max_length=255, blank=True)
    postal_state = models.CharField(max_length=255, default='WA', blank=True)
    postal_country = CountryField(default='AU', blank=True)
    postal_postcode = models.CharField(max_length=10, blank=True)

    # Contact
    email = models.EmailField(null=True, blank=True,)
    phone_number = models.CharField(max_length=50, null=True, blank=True, verbose_name="phone number", help_text='')
    mobile_number = models.CharField(max_length=50, null=True, blank=True, verbose_name="mobile number", help_text='')
    
    created_at = models.DateTimeField(blank=True, null=True, auto_now_add=True)
    updated_at = models.DateTimeField(blank=True, null=True, auto_now=True)

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        return f'{self.email}: {self.first_name} {self.last_name} (ID: {self.id})'

    @property
    def postal_address_line1(self):
        if self.postal_same_as_residential:
            return self.residential_line1
        else:
            return self.postal_line1

    @property
    def postal_address_line2(self):
        if self.postal_same_as_residential:
            return self.residential_line2
        else:
            return self.postal_line2

    @property
    def postal_address_state(self):
        if self.postal_same_as_residential:
            return self.residential_state
        else:
            return self.postal_state

    @property
    def postal_address_suburb(self):
        if self.postal_same_as_residential:
            return self.residential_locality
        else:
            return self.postal_locality

    @property
    def postal_address_postcode(self):
        if self.postal_same_as_residential:
            return self.residential_postcode
        else:
            return self.postal_postcode
        
    def get_full_name(self):
        full_name = '{} {}'.format(self.first_name, self.last_name)
        return full_name

    def copy_self_to_proposal(self, target_proposal):
        proposal_applicant = ProposalApplicant.objects.create(
            proposal=target_proposal,

            first_name = self.first_name,
            last_name = self.last_name,
            dob = self.dob,

            residential_line1 = self.residential_line1,
            residential_line2 = self.residential_line2,
            residential_line3 = self.residential_line3,
            residential_locality = self.residential_locality,
            residential_state = self.residential_state,
            residential_country = self.residential_country,
            residential_postcode = self.residential_postcode,

            postal_same_as_residential = self.postal_same_as_residential,
            postal_line1 = self.postal_line1,
            postal_line2 = self.postal_line2,
            postal_line3 = self.postal_line3,
            postal_locality = self.postal_locality,
            postal_state = self.postal_state,
            postal_country = self.postal_country,
            postal_postcode = self.postal_postcode,

            email = self.email,
            phone_number = self.phone_number,
            mobile_number = self.mobile_number,
        )
        logger.info(f'ProposalApplicant: [{proposal_applicant}] has been created for the Proposal: [{target_proposal}] by copying the ProposalApplicant: [{self}].')


def update_sticker_doc_filename(instance, filename):
    return '{}/stickers/batch/{}'.format(settings.MEDIA_APP_DIR, filename)


def update_sticker_response_doc_filename(instance, filename):
    return '{}/stickers/response/{}'.format(settings.MEDIA_APP_DIR, filename)


def update_sticker_doc_filename(instance, filename):
    return '{}/stickers/batch/{}'.format(settings.MEDIA_APP_DIR, filename)


def update_sticker_response_doc_filename(instance, filename):
    return '{}/stickers/response/{}'.format(settings.MEDIA_APP_DIR, filename)


class StickerPrintingContact(models.Model):
    TYPE_EMIAL_TO = 'to'
    TYPE_EMAIL_CC = 'cc'
    TYPE_EMAIL_BCC = 'bcc'
    TYPES = (
        (TYPE_EMIAL_TO, 'To'),
        (TYPE_EMAIL_CC, 'Cc'),
        (TYPE_EMAIL_BCC, 'Bcc'),
    )
    email = models.EmailField(blank=True, null=True)
    type = models.CharField(max_length=255, choices=TYPES, blank=False, null=False,)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return '{} ({})'.format(self.email, self.type)

    class Meta:
        app_label = 'mooringlicensing'


class StickerPrintedContact(models.Model):
    TYPE_EMIAL_TO = 'to'
    TYPE_EMAIL_CC = 'cc'
    TYPE_EMAIL_BCC = 'bcc'
    TYPES = (
        (TYPE_EMIAL_TO, 'To'),
        (TYPE_EMAIL_CC, 'Cc'),
        (TYPE_EMAIL_BCC, 'Bcc'),
    )
    email = models.EmailField(blank=True, null=True)
    type = models.CharField(max_length=255, choices=TYPES, blank=False, null=False,)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return '{} ({})'.format(self.email, self.type)

    class Meta:
        app_label = 'mooringlicensing'


class StickerPrintingBatch(Document):
    _file = models.FileField(storage=private_storage,upload_to=update_sticker_doc_filename, max_length=512)
    emailed_datetime = models.DateTimeField(blank=True, null=True)  # Once emailed, this field has a value

    class Meta:
        app_label = 'mooringlicensing'


class StickerPrintingResponseEmail(models.Model):
    email_subject = models.CharField(max_length=255, blank=True, null=True)
    email_body = models.TextField(null=True, blank=True)
    email_date = models.CharField(max_length=255, blank=True, null=True)
    email_from = models.CharField(max_length=255, blank=True, null=True)
    email_message_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        return f'Id: {self.id}, subject: {self.email_subject}'


class StickerPrintingResponse(Document):
    _file = models.FileField(storage=private_storage,upload_to=update_sticker_response_doc_filename, max_length=512)
    sticker_printing_response_email = models.ForeignKey(StickerPrintingResponseEmail, blank=True, null=True, on_delete=models.SET_NULL)
    processed = models.BooleanField(default=False)  # Processed by a cron to update sticker details
    no_errors_when_process = models.NullBooleanField(default=None)

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        if self._file:
            return f'Id: {self.id}, {self._file.url}'
        else:
            return f'Id: {self.id}'

    @property
    def email_subject(self):
        if self.sticker_printing_response_email:
            return self.sticker_printing_response_email.email_subject
        return ''

    @property
    def email_date(self):
        if self.sticker_printing_response_email:
            return self.sticker_printing_response_email.email_date
        return ''


class WaitingListApplication(Proposal):
    proposal = models.OneToOneField(Proposal, parent_link=True, on_delete=models.CASCADE)
    code = 'wla'
    prefix = 'WL'

    new_application_text = "I want to be included on the waiting list for a mooring site licence"

    apply_page_visibility = True
    description = 'Waiting List Application'

    class Meta:
        app_label = 'mooringlicensing'
    
    def validate_against_existing_proposals_and_approvals(self):
        from mooringlicensing.components.approvals.models import Approval, ApprovalHistory, WaitingListAllocation, MooringLicence
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        # Get blocking proposals 
        # Checking if there are any applications still in process, but it might not be necessary to check for that because the blocking approvals are checked below.
        proposals = Proposal.objects.filter(
            Q(vessel_details__vessel=self.vessel_ownership.vessel) &
            (Q(vessel_ownership__end_date__gt=today) | Q(vessel_ownership__end_date__isnull=True))  # Vessel has not been sold yet
        ).exclude(id=self.id)
        child_proposals = [proposal.child_obj for proposal in proposals]
        logger.debug(f'child_proposals: [{child_proposals}]')
        # proposals_wla = []
        # proposals_mla = []
        blocking_proposals = []  # 
        for proposal in child_proposals:
            if proposal.processing_status not in [
                # Proposal.PROCESSING_STATUS_APPROVED, 
                Proposal.PROCESSING_STATUS_DECLINED, 
                Proposal.PROCESSING_STATUS_EXPIRED, 
                Proposal.PROCESSING_STATUS_DISCARDED,
            ] and proposal.succeeding_proposals.count() == 0: # There are no succeeding proposals, which means this proposal is the lastest proposal.
                # if type(proposal) == WaitingListApplication:
                #     proposals_wla.append(proposal)
                # if type(proposal) == MooringLicenceApplication:
                #     proposals_mla.append(proposal)
                blocking_proposals.append(proposal)

        # Get blocking approvals
        approval_histories = ApprovalHistory.objects.filter(
            Q(end_date=None) &
            Q(vessel_ownership__vessel=self.vessel_ownership.vessel) &
            (Q(vessel_ownership__end_date__gt=today) | Q(vessel_ownership__end_date__isnull=True))  # Vessel has not been sold yet
        ).exclude(approval_id=self.approval_id)
        approvals = [ah.approval for ah in approval_histories]
        approvals = list(dict.fromkeys(approvals))  # remove duplicateS
        # approvals_wla = []
        # approvals_ml = []
        blocking_approvals = []
        for approval in approvals:
            if approval.status in Approval.APPROVED_STATUSES:
                # if type(approval.child_obj) == WaitingListAllocation:
                #     approvals_wla.append(approval)
                # if type(approval.child_obj) == MooringLicence:
                #     approvals_ml.append(approval)
                blocking_approvals.append(approval)

        # if (proposals_wla or approvals_wla or proposals_mla or approvals_ml):
        if (blocking_proposals or blocking_approvals):
            bp = ", ".join(['{} {} '.format(proposal.description, proposal.lodgement_number) for proposal in blocking_proposals])
            ba = ", ".join(['{} {} '.format(approval.description, approval.lodgement_number) for approval in blocking_approvals])
            msg = f'The vessel: {self.vessel_ownership.vessel} in the application is already listed in {bp}{ba}'
            # raise serializers.ValidationError("The vessel in the application is already listed in " +
            # ", ".join(['{} {} '.format(proposal.description, proposal.lodgement_number) for proposal in proposals_wla]) +
            # ", ".join(['{} {} '.format(proposal.description, proposal.lodgement_number) for proposal in blocking_proposals]) +
            # ", ".join(['{} {} '.format(approval.description, approval.lodgement_number) for approval in approvals_wla])
            # ", ".join(['{} {} '.format(approval.description, approval.lodgement_number) for approval in blocking_approvals])
            # )
            logger.error(msg)
            raise serializers.ValidationError(msg)
        # Person can have only one WLA, Waiting Liast application, Mooring Licence and Mooring Licence application
        elif (
                WaitingListApplication.get_intermediate_proposals(self.submitter).exclude(id=self.id) or
                WaitingListAllocation.get_intermediate_approvals(self.submitter).exclude(approval=self.approval) or
                MooringLicenceApplication.get_intermediate_proposals(self.submitter) or
                MooringLicence.get_valid_approvals(self.submitter)
            ):
            msg = "Person can have only one WLA, Waiting List application, Mooring Site Licence and Mooring Site Licence application"
            logger.error(msg)
            raise serializers.ValidationError(msg)
    
    def validate_vessel_length(self, request):
        min_mooring_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINUMUM_MOORING_VESSEL_LENGTH).value
        min_mooring_vessel_size = float(min_mooring_vessel_size_str)

        if self.vessel_details.vessel_applicable_length < min_mooring_vessel_size:
            logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_mooring_vessel_size_str))
            raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_mooring_vessel_size_str))

    def process_after_discarded(self):
        logger.debug(f'called in [{self}]')

    def process_after_withdrawn(self):
        logger.debug(f'called in [{self}]')

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    @staticmethod
    def get_intermediate_proposals(email_user_id):
        proposals = WaitingListApplication.objects.filter(submitter=email_user_id).exclude(processing_status__in=[
            Proposal.PROCESSING_STATUS_APPROVED,
            Proposal.PROCESSING_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_DISCARDED,
        ])
        return proposals

    def create_fee_lines(self):
        """
        Create the ledger lines - line item for application fee sent to payment system
        """
        logger.info(f'Creating fee lines for the WaitingListApplication: [{self}]...')

        from mooringlicensing.components.payments_ml.models import FeeConstructor
        from mooringlicensing.components.payments_ml.utils import generate_line_item

        current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        current_datetime_str = current_datetime.astimezone(pytz.timezone(TIME_ZONE)).strftime('%d/%m/%Y %I:%M %p')
        target_date = self.get_target_date(current_datetime.date())
        logger.info('Creating fee lines for the proposal: [{}], target date: {}'.format(self, target_date))

        # Any changes to the DB should be made after the success of payment process
        db_processes_after_success = {}
        accept_null_vessel = False

        application_type = self.application_type

        if self.vessel_details:
            vessel_length = self.vessel_details.vessel_applicable_length
        else:
            # No vessel specified in the application
            if self.does_accept_null_vessel:
                # For the amendment application or the renewal application, vessel field can be blank when submit.
                vessel_length = -1
                accept_null_vessel = True
            else:
                # msg = 'No vessel specified for the application {}'.format(self.lodgement_number)
                msg = 'The application fee admin data has not been set up correctly for the Waiting List application type.  Please contact the Rottnest Island Authority.'
                logger.error(msg)
                raise Exception(msg)

        logger.info(f'vessel_length: {vessel_length}')

        # Retrieve FeeItem object from FeeConstructor object
        fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(application_type, target_date)

        logger.info(f'FeeConstructor (for main component(WL)): {fee_constructor}')

        if not fee_constructor:
            # Fees have not been configured for this application type and date
            msg = 'FeeConstructor object for the ApplicationType: {} not found for the date: {} for the application: {}'.format(
                application_type, target_date, self.lodgement_number)
            logger.error(msg)
            raise Exception(msg)

        # Retrieve amounts paid
        max_amount_paid = self.get_max_amount_paid_for_main_component()
        logger.info(f'Max amount paid so far (for main component(WL)): ${max_amount_paid}')
        fee_item = fee_constructor.get_fee_item(vessel_length, self.proposal_type, target_date, accept_null_vessel=accept_null_vessel)
        logger.info(f'FeeItem (for main component(WL)): [{fee_item}] has been retrieved for calculation.')
        fee_amount_adjusted = self.get_fee_amount_adjusted(fee_item, vessel_length, max_amount_paid)
        logger.info(f'Fee amount adjusted (for main component(WL)) to be paid: ${fee_amount_adjusted}')

        db_processes_after_success['season_start_date'] = fee_constructor.fee_season.start_date.__str__()
        db_processes_after_success['season_end_date'] = fee_constructor.fee_season.end_date.__str__()
        db_processes_after_success['datetime_for_calculating_fee'] = current_datetime_str
        db_processes_after_success['fee_item_id'] = fee_item.id if fee_item else 0
        db_processes_after_success['fee_amount_adjusted'] = str(fee_amount_adjusted)

        line_items = []
        line_items.append(
            generate_line_item(application_type, fee_amount_adjusted, fee_constructor, self, current_datetime))

        logger.info(f'line_items calculated: {line_items}')

        return line_items, db_processes_after_success

    @property
    def assessor_group(self):
        return ledger_api_client.managed_models.SystemGroup.objects.get(name="Mooring Licensing - Assessors: Waiting List")

    @property
    def approver_group(self):
        return None

    @property
    def assessor_recipients(self):
        return [retrieve_email_userro(id).email for id in self.assessor_group.get_system_group_member_ids()]

    @property
    def approver_recipients(self):
        return []

    def is_assessor(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        # return user in self.assessor_group.user_set.all()
        return user in self.assessor_group.get_system_group_member_ids()

    #def is_approver(self, user):
     #   return False

    def is_approver(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        #return user in self.approver_group.user_set.all()
        # return user in self.assessor_group.user_set.all()
        return user in self.assessor_group.get_system_group_member_ids()

    def save(self, *args, **kwargs):
        super(WaitingListApplication, self).save(*args, **kwargs)
        if self.lodgement_number == '':
            new_lodgment_id = '{1}{0:06d}'.format(self.proposal_id, self.prefix)
            self.lodgement_number = new_lodgment_id
            self.save()
        self.proposal.refresh_from_db()

    def send_emails_after_payment_success(self, request):
        attachments = []
        if self.invoice:
            # invoice_bytes = create_invoice_pdf_bytes('invoice.pdf', self.invoice,)
            # api_key = settings.LEDGER_API_KEY
            # url = settings.LEDGER_API_URL + '/ledgergw/invoice-pdf/' + api_key + '/' + self.invoice.reference
            # url = get_invoice_url(self.invoice.reference, request)
            # invoice_pdf = requests.get(url=url)

            api_key = settings.LEDGER_API_KEY
            url = settings.LEDGER_API_URL+'/ledgergw/invoice-pdf/'+api_key+'/' + self.invoice.reference
            invoice_pdf = requests.get(url=url)

            if invoice_pdf.status_code == 200:
                attachment = ('invoice#{}.pdf'.format(self.invoice.reference), invoice_pdf.content, 'application/pdf')
                attachments.append(attachment)
        try:
            ret_value = send_confirmation_email_upon_submit(request, self, True, attachments)
            if not self.auto_approve:
                send_notification_email_upon_submit_to_assessor(request, self, attachments)
        except Exception as e:
            logger.exception("Error when sending confirmation/notification email upon submit.", exc_info=True)


    @property
    def does_accept_null_vessel(self):
        if self.proposal_type.code in [PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL,]:
            return True
        # return False

    def process_after_approval(self, request=None, total_amount=0):
        pass

    def does_have_valid_associations(self):
        """
        Check if this application has valid associations with other applications and approvals
        """
        # TODO: correct the logic.  just partially implemented
        valid = True

        # Rules for proposal
        proposals = WaitingListApplication.objects.\
            filter(vessel_details__vessel=self.vessel_details.vessel).\
            exclude(
                Q(id=self.id) | Q(processing_status__in=(Proposal.PROCESSING_STATUS_DECLINED, Proposal.PROCESSING_STATUS_APPROVED, Proposal.PROCESSING_STATUS_DISCARDED))
            )
        if proposals:
            # The vessel in this application is already part of another application
            valid = False

        return valid


class AnnualAdmissionApplication(Proposal):
    proposal = models.OneToOneField(Proposal, parent_link=True, on_delete=models.CASCADE)
    code = 'aaa'
    prefix = 'AA'
    new_application_text = "I want to apply for an annual admission permit"
    apply_page_visibility = True
    description = 'Annual Admission Application'

    def validate_against_existing_proposals_and_approvals(self):
        from mooringlicensing.components.approvals.models import Approval, ApprovalHistory, AnnualAdmissionPermit, MooringLicence, AuthorisedUserPermit
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        # Get blocking proposals
        proposals = Proposal.objects.filter(
            vessel_details__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(id=self.id)
        child_proposals = [proposal.child_obj for proposal in proposals]
        proposals_mla = []
        proposals_aaa = []
        proposals_aua = []
        for proposal in child_proposals:
            if proposal.processing_status not in [
                Proposal.PROCESSING_STATUS_APPROVED, 
                Proposal.PROCESSING_STATUS_DECLINED, 
                Proposal.PROCESSING_STATUS_DISCARDED,
            ]:
                if type(proposal) == MooringLicenceApplication:
                    proposals_mla.append(proposal)
                if type(proposal) == AnnualAdmissionApplication:
                    proposals_aaa.append(proposal)
                if type(proposal) == AuthorisedUserApplication:
                    proposals_aua.append(proposal)

        # Get blocking approvals
        approval_histories = ApprovalHistory.objects.filter(
            end_date=None,
            vessel_ownership__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(approval_id=self.approval_id)
        approvals = [ah.approval for ah in approval_histories]
        approvals = list(dict.fromkeys(approvals))  # remove duplicates
        approvals_ml = []
        approvals_aap = []
        approvals_aup = []
        for approval in approvals:
            if approval.status in Approval.APPROVED_STATUSES:
                if type(approval.child_obj) == MooringLicence:
                    approvals_ml.append(approval)
                if type(approval.child_obj) == AnnualAdmissionPermit:
                    approvals_aap.append(approval)
                if type(approval.child_obj) == AuthorisedUserPermit:
                    approvals_aup.append(approval)

        if proposals_aaa or approvals_aap or proposals_aua or approvals_aup or proposals_mla or approvals_ml:
            list_sum = proposals_aaa + proposals_aua + proposals_mla + approvals_aap + approvals_aup + approvals_ml
            raise serializers.ValidationError("The vessel in the application is already listed in " +
            ", ".join(['{} {} '.format(item.description, item.lodgement_number) for item in list_sum]))

    def validate_vessel_length(self, request):
        min_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINIMUM_VESSEL_LENGTH).value
        min_vessel_size = float(min_vessel_size_str)

        if self.vessel_details.vessel_applicable_length < min_vessel_size:
            logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_vessel_size_str))
            raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_vessel_size_str))

    def process_after_discarded(self):
        logger.debug(f'called in [{self}]')

    def process_after_withdrawn(self):
        logger.debug(f'called in [{self}]')

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    def create_fee_lines(self):
        """
        Create the ledger lines - line item for application fee sent to payment system
        """
        logger.info(f'Creating fee lines for the AnnualAdmissionApplication: [{self}]...')

        from mooringlicensing.components.payments_ml.models import FeeConstructor
        from mooringlicensing.components.payments_ml.utils import generate_line_item

        current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        current_datetime_str = current_datetime.astimezone(pytz.timezone(TIME_ZONE)).strftime('%d/%m/%Y %I:%M %p')
        target_date = self.get_target_date(current_datetime.date())
        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)  # Used for AUA / MLA

        logger.info('Creating fee lines for the proposal: [{}], target date: {}'.format(self, target_date))

        # Any changes to the DB should be made after the success of payment process
        db_processes_after_success = {}
        accept_null_vessel = False

        if self.vessel_details:
            vessel_length = self.vessel_details.vessel_applicable_length
        else:
            # No vessel specified in the application
            if self.does_accept_null_vessel:
                # For the amendment application or the renewal application, vessel field can be blank when submit.
                vessel_length = -1
                accept_null_vessel = True
            else:
                # msg = 'No vessel specified for the application {}'.format(self.lodgement_number)
                msg = 'The application fee admin data has not been set up correctly for the Annual Admission Permit application type.  Please contact the Rottnest Island Authority.'
                logger.error(msg)
                raise Exception(msg)

        logger.info(f'vessel_length: {vessel_length}')

        # Retrieve FeeItem object from FeeConstructor object
        fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(self.application_type, target_date)

        logger.info(f'FeeConstructor (for main component(AA)): {fee_constructor}')

        if self.application_type.code in (AuthorisedUserApplication.code, MooringLicenceApplication.code):
            # There is also annual admission fee component for the AUA/MLA.
            fee_constructor_for_aa = FeeConstructor.get_fee_constructor_by_application_type_and_date(annual_admission_type, target_date)
            if not fee_constructor_for_aa:
                # Fees have not been configured for the annual admission application and date
                msg = 'FeeConstructor object for the Annual Admission Application not found for the date: {} for the application: {}'.format(target_date, self.lodgement_number)
                logger.error(msg)
                raise Exception(msg)
        if not fee_constructor:
            # Fees have not been configured for this application type and date
            msg = 'FeeConstructor object for the ApplicationType: {} not found for the date: {} for the application: {}'.format(self.application_type, target_date, self.lodgement_number)
            logger.error(msg)
            raise Exception(msg)

        # Retrieve amounts paid
        max_amount_paid = self.get_max_amount_paid_for_main_component()
        logger.info(f'Max amount paid so far (for main component(AA)): ${max_amount_paid}')
        fee_item = fee_constructor.get_fee_item(vessel_length, self.proposal_type, target_date, accept_null_vessel=accept_null_vessel)
        logger.info(f'FeeItem (for main component(AA)): [{fee_item}] has been retrieved for calculation.')
        fee_amount_adjusted = self.get_fee_amount_adjusted(fee_item, vessel_length, max_amount_paid)
        logger.info(f'Fee amount adjusted (for main component(AA)) to be paid: ${fee_amount_adjusted}')

        db_processes_after_success['season_start_date'] = fee_constructor.fee_season.start_date.__str__()
        db_processes_after_success['season_end_date'] = fee_constructor.fee_season.end_date.__str__()
        db_processes_after_success['datetime_for_calculating_fee'] = current_datetime_str
        db_processes_after_success['fee_item_id'] = fee_item.id if fee_item else 0
        db_processes_after_success['fee_amount_adjusted'] = str(fee_amount_adjusted)

        line_items = []
        line_items.append(generate_line_item(self.application_type, fee_amount_adjusted, fee_constructor, self, current_datetime))

        logger.info(f'line_items calculated: {line_items}')

        return line_items, db_processes_after_success

    @property
    def assessor_group(self):
        # return Group.objects.get(name="Mooring Licensing - Assessors: Annual Admission")
        return ledger_api_client.managed_models.SystemGroup.objects.get(name="Mooring Licensing - Assessors: Annual Admission")

    @property
    def approver_group(self):
        return None

    @property
    def assessor_recipients(self):
        # return [i.email for i in self.assessor_group.user_set.all()]
        return [retrieve_email_userro(id).email for id in self.assessor_group.get_system_group_member_ids()]

    @property
    def approver_recipients(self):
        return []

    def is_assessor(self, user):
        # return user in dself.assessor_group.user_set.all()
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.assessor_group.get_system_group_member_ids()

    #def is_approver(self, user):
     #   return False

    def is_approver(self, user):
        # return user in self.assessor_group.user_set.all()
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.assessor_group.get_system_group_member_ids()

    def save(self, *args, **kwargs):
        #application_type_acronym = self.application_type.acronym if self.application_type else None
        super(AnnualAdmissionApplication, self).save(*args,**kwargs)
        if self.lodgement_number == '':
            new_lodgment_id = '{1}{0:06d}'.format(self.proposal_id, self.prefix)
            self.lodgement_number = new_lodgment_id
            self.save()
        self.proposal.refresh_from_db()

    def send_emails_after_payment_success(self, request):
        attachments = []
        if self.invoice:
        #     invoice_bytes = create_invoice_pdf_bytes('invoice.pdf', self.invoice,)
        #     attachment = ('invoice#{}.pdf'.format(self.invoice.reference), invoice_bytes, 'application/pdf')
        #     attachments.append(attachment)
        #     url = get_invoice_url(self.invoice.reference, request)
        #     invoice_pdf = requests.get(url=url)
            api_key = settings.LEDGER_API_KEY
            url = settings.LEDGER_API_URL+'/ledgergw/invoice-pdf/'+api_key+'/' + self.invoice.reference
            invoice_pdf = requests.get(url=url)

            if invoice_pdf.status_code == 200:
                attachment = (f'invoice#{self.invoice.reference}', invoice_pdf.content, 'application/pdf')
                attachments.append(attachment)
        if not self.auto_approve:
            try:
                send_confirmation_email_upon_submit(request, self, True, attachments)
                send_notification_email_upon_submit_to_assessor(request, self, attachments)
            except Exception as e:
                logger.exception("Error when sending confirmation/notification email upon submit.", exc_info=True)


    def process_after_approval(self, request=None, total_amount=0):
        pass

    @property
    def does_accept_null_vessel(self):
        # if self.proposal_type.code in (PROPOSAL_TYPE_AMENDMENT,):
        #     return True
        return False

    def does_have_valid_associations(self):
        """
        Check if this application has valid associations with other applications and approvals
        """
        # TODO: implement logic
        return True


class AuthorisedUserApplication(Proposal):
    proposal = models.OneToOneField(Proposal, parent_link=True, on_delete=models.CASCADE)
    code = 'aua'
    prefix = 'AU'
    new_application_text = "I want to apply for an authorised user permit"
    apply_page_visibility = True
    description = 'Authorised User Application'

    # This uuid is used to generate the URL for the AUA endorsement link
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)

    def validate_against_existing_proposals_and_approvals(self):
        from mooringlicensing.components.approvals.models import Approval, ApprovalHistory, AuthorisedUserPermit
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        # Get blocking proposals
        proposals = Proposal.objects.filter(
            vessel_details__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(id=self.id)
        child_proposals = [proposal.child_obj for proposal in proposals]
        proposals_aua = []
        for proposal in child_proposals:
            if proposal.processing_status not in [
                Proposal.PROCESSING_STATUS_APPROVED,
                Proposal.PROCESSING_STATUS_DECLINED,
                Proposal.PROCESSING_STATUS_DISCARDED,
            ]:
                if type(proposal) == AuthorisedUserApplication:
                    proposals_aua.append(proposal)

        # Get blocking approvals
        approval_histories = ApprovalHistory.objects.filter(
            end_date=None,
            vessel_ownership__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(approval_id=self.approval_id)
        approvals = [ah.approval for ah in approval_histories]
        approvals = list(dict.fromkeys(approvals))  # remove duplicates
        approvals_aup = []
        for approval in approvals:
            if approval.status in Approval.APPROVED_STATUSES:
                if type(approval.child_obj) == AuthorisedUserPermit:
                    approvals_aup.append(approval)

        if proposals_aua or approvals_aup:
            #association_fail = True
            raise serializers.ValidationError("The vessel in the application is already listed in " +  
                ", ".join(['{} {} '.format(proposal.description, proposal.lodgement_number) for proposal in proposals_aua]) +
                ", ".join(['{} {} '.format(approval.description, approval.lodgement_number) for approval in approvals_aup])
            )

    def validate_vessel_length(self, request):
        min_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINIMUM_VESSEL_LENGTH).value
        min_vessel_size = float(min_vessel_size_str)

        if self.vessel_details.vessel_applicable_length < min_vessel_size:
            logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_vessel_size_str))
            raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_vessel_size_str))

        # check new site licensee mooring
        proposal_data = request.data.get('proposal') if request.data.get('proposal') else {}
        mooring_id = proposal_data.get('mooring_id')
        if mooring_id and proposal_data.get('site_licensee_email'):
            mooring = Mooring.objects.get(id=mooring_id)
            if (self.vessel_details.vessel_applicable_length > mooring.vessel_size_limit or
            self.vessel_details.vessel_draft > mooring.vessel_draft_limit):
                logger.error("Proposal {}: Vessel unsuitable for mooring".format(self))
                raise serializers.ValidationError("Vessel unsuitable for mooring")
        if self.approval:
            # Amend / Renewal
            if proposal_data.get('keep_existing_mooring'):
                # check existing moorings against current vessel dimensions
                for moa in self.approval.mooringonapproval_set.filter(end_date__isnull=True):
                    if self.vessel_details.vessel_applicable_length > moa.mooring.vessel_size_limit:
                        logger.error(f"Vessel applicable lentgh: [{self.vessel_details.vessel_applicable_length}] is not suitable for the mooring: [{moa.mooring}]")
                        raise serializers.ValidationError(f"Vessel length: {self.vessel_details.vessel_applicable_length}[m] is not suitable for the vessel size limit: {moa.mooring.vessel_size_limit} [m] of the mooring: [{moa.mooring}]")
                    if self.vessel_details.vessel_draft > moa.mooring.vessel_draft_limit:
                        logger.error(f"Vessel draft: [{self.vessel_details.vessel_draft}] is not suitable for the mooring: [{moa.mooring}]")
                        raise serializers.ValidationError(f"Vessel draft: {self.vessel_details.vessel_draft} [m] is not suitable for the vessel draft limit: {moa.mooring.vessel_draft_limit} [m] of the mooring: [{moa.mooring}]")

    def process_after_discarded(self):
        logger.debug(f'called in [{self}]')

    def process_after_withdrawn(self):
        logger.debug(f'called in [{self}]')

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    def create_fee_lines(self):
        """ Create the ledger lines - line item for application fee sent to payment system """
        logger.info(f'Creating fee lines for the AuthorisedUserApplication: [{self}]...')

        from mooringlicensing.components.payments_ml.models import FeeConstructor
        from mooringlicensing.components.payments_ml.utils import generate_line_item

        current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        target_date = self.get_target_date(current_datetime.date())
        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)  # Used for AUA / MLA
        accept_null_vessel = False

        logger.info('Creating fee lines for the proposal: [{}], target date: {}'.format(self, target_date))

        if self.vessel_details:
            vessel_length = self.vessel_details.vessel_applicable_length
        else:
            # No vessel specified in the application
            if self.does_accept_null_vessel:
                # For the amendment application or the renewal application, vessel field can be blank when submit.
                vessel_length = -1
                accept_null_vessel = True
            else:
                # msg = 'No vessel specified for the application {}'.format(self.lodgement_number)
                msg = 'The application fee admin data has not been set up correctly for the Authorised User Permit application type.  Please contact the Rottnest Island Authority.'
                logger.error(msg)
                raise Exception(msg)

        logger.info(f'vessel_length: {vessel_length}')

        # Retrieve FeeItem object from FeeConstructor object
        fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(self.application_type, target_date)
        fee_constructor_for_aa = FeeConstructor.get_fee_constructor_by_application_type_and_date(annual_admission_type, target_date)

        logger.info(f'FeeConstructor (for main component(AU)): {fee_constructor}')
        logger.info(f'FeeConstructor (for AA component): {fee_constructor_for_aa}')

        # There is also annual admission fee component for the AUA/MLA if needed.
        ml_exists_for_this_vessel = False
        application_has_vessel = True if self.vessel_details else False

        if application_has_vessel:
            # When there is a vessel in this application
            current_approvals_dict = self.vessel_details.vessel.get_current_approvals(target_date)
            for key, approvals in current_approvals_dict.items():
                if key == 'mls' and approvals.count():
                    ml_exists_for_this_vessel = True

            if ml_exists_for_this_vessel:
                logger.info(f'ML for the vessel: {self.vessel_details.vessel} exists. No charges for the AUP: {self}')

                # When there is 'current' ML, no charge for the AUP
                # But before leaving here, we want to store the fee_season under this application the user is applying for.
                self.fee_season = fee_constructor.fee_season
                self.save()

                logger.info(f'FeeSeason: {fee_constructor.fee_season} is saved under the proposal: {self}')
                fee_lines = [generate_line_item(self.application_type, 0, fee_constructor, self, current_datetime),]

                return fee_lines, {}  # no line items, no db process
            else:
                logger.info(f'ML for the vessel: {self.vessel_details.vessel} does not exist.')

        else:
            # Null vessel application
            logger.info(f'This is null vessel application')

        fee_items_to_store = []
        line_items = []

        # Retrieve amounts paid
        max_amount_paid = self.get_max_amount_paid_for_main_component()
        logger.info(f'Max amount paid so far (for main component(AU)): ${max_amount_paid}')
        fee_item = fee_constructor.get_fee_item(vessel_length, self.proposal_type, target_date, accept_null_vessel=accept_null_vessel)
        logger.info(f'FeeItem (for main component(AU)): [{fee_item}] has been retrieved for calculation.')
        fee_amount_adjusted = self.get_fee_amount_adjusted(fee_item, vessel_length, max_amount_paid)
        logger.info(f'Fee amount adjusted (for main component(AU)) to be paid: ${fee_amount_adjusted}')

        fee_items_to_store.append({
            'fee_item_id': fee_item.id,
            'vessel_details_id': self.vessel_details.id if self.vessel_details else '',
            'fee_amount_adjusted': str(fee_amount_adjusted),
        })
        line_items.append(generate_line_item(self.application_type, fee_amount_adjusted, fee_constructor, self, current_datetime))

        if application_has_vessel:
            # When the application has a vessel, user have to pay for the AA component, too.
            max_amount_paid = self.get_max_amount_paid_for_aa_component(target_date, self.vessel_details.vessel)
            logger.info(f'Max amount paid so far (for AA component): ${max_amount_paid}')
            fee_item_for_aa = fee_constructor_for_aa.get_fee_item(vessel_length, self.proposal_type, target_date) if fee_constructor_for_aa else None
            logger.info(f'FeeItem (for AA component): [{fee_item_for_aa}] has been retrieved for calculation.')
            fee_amount_adjusted_additional = self.get_fee_amount_adjusted(fee_item_for_aa, vessel_length, max_amount_paid)
            logger.info(f'Fee amount adjusted (for AA component) to be paid: ${fee_amount_adjusted_additional}')

            fee_items_to_store.append({
                'fee_item_id': fee_item_for_aa.id,
                'vessel_details_id': self.vessel_details.id if self.vessel_details else '',
                'fee_amount_adjusted': str(fee_amount_adjusted_additional),
            })
            line_items.append(generate_line_item(annual_admission_type, fee_amount_adjusted_additional, fee_constructor_for_aa, self, current_datetime))

        logger.info(f'line_items calculated: {line_items}')

        return line_items, fee_items_to_store

    def get_due_date_for_endorsement_by_target_date(self, target_date=timezone.localtime(timezone.now()).date()):
        days_type = NumberOfDaysType.objects.get(code=CODE_DAYS_FOR_ENDORSER_AUA)
        days_setting = NumberOfDaysSetting.get_setting_by_date(days_type, target_date)
        if not days_setting:
            # No number of days found
            raise ImproperlyConfigured("NumberOfDays: {} is not defined for the date: {}".format(days_type.name, target_date))
        due_date = self.lodgement_date + datetime.timedelta(days=days_setting.number_of_days)
        return due_date

    @property
    def assessor_group(self):
        return ledger_api_client.managed_models.SystemGroup.objects.get(name="Mooring Licensing - Assessors: Authorised User")

    @property
    def approver_group(self):
        return ledger_api_client.managed_models.SystemGroup.objects.get(name="Mooring Licensing - Approvers: Authorised User")

    @property
    def assessor_recipients(self):
        return [retrieve_email_userro(i).email for i in self.assessor_group.get_system_group_member_ids()]

    @property
    def approver_recipients(self):
        return [retrieve_email_userro(i).email for i in self.approver_group.get_system_group_member_ids()]

    def is_assessor(self, user):
        # return user in self.assessor_group.user_set.all()
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.assessor_group.get_system_group_member_ids()

    def is_approver(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.approver_group.get_system_group_member_ids()

    def save(self, *args, **kwargs):
        super(AuthorisedUserApplication, self).save(*args, **kwargs)
        if self.lodgement_number == '':
            new_lodgment_id = '{1}{0:06d}'.format(self.proposal_id, self.prefix)
            self.lodgement_number = new_lodgment_id
            self.save()
        self.proposal.refresh_from_db()

    def send_emails_after_payment_success(self, request):
        # ret_value = send_submit_email_notification(request, self)
        # TODO: Send payment success email to the submitter (applicant)
        return True

    def get_mooring_authorisation_preference(self):
        if self.keep_existing_mooring and self.previous_application:
            return self.previous_application.child_obj.get_mooring_authorisation_preference()
        else:
            return self.mooring_authorisation_preference

    def process_after_submit(self, request):
        self.lodgement_date = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        self.save()
        self.log_user_action(ProposalUserAction.ACTION_LODGE_APPLICATION.format(self.lodgement_number), request)
        mooring_preference = self.get_mooring_authorisation_preference()

        # if mooring_preference.lower() != 'ria' and self.proposal_type.code in [PROPOSAL_TYPE_NEW,]:
        if ((mooring_preference.lower() != 'ria' and self.proposal_type.code == PROPOSAL_TYPE_NEW) or
            (mooring_preference.lower() != 'ria' and self.proposal_type.code != PROPOSAL_TYPE_NEW and not self.keep_existing_mooring)):
            # Mooring preference is 'site_licensee' and which is new mooring applying for.
            self.processing_status = Proposal.PROCESSING_STATUS_AWAITING_ENDORSEMENT
            self.save()
            # Email to endorser
            send_endorsement_of_authorised_user_application_email(request, self)
            send_confirmation_email_upon_submit(request, self, False)
        else:
            self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
            self.save()
            send_confirmation_email_upon_submit(request, self, False)
            if not self.auto_approve:
                send_notification_email_upon_submit_to_assessor(request, self)

    def update_or_create_approval(self, current_datetime, request=None):
        logger.info(f'Updating/Creating Authorised User Permit from the application: [{self}]...')
        # This function is called after payment success for new/amendment/renewal application

        created = None

        # Manage approval
        approval_created = False
        if self.proposal_type.code == PROPOSAL_TYPE_NEW:
            # When new application
            approval, approval_created = self.approval_class.objects.update_or_create(
                current_proposal=self,
                defaults={
                    'issue_date': current_datetime,
                    'start_date': current_datetime.date(),
                    'expiry_date': self.end_date,
                    'submitter': self.submitter,
                }
            )
            if approval_created:
                from mooringlicensing.components.approvals.models import Approval
                logger.info(f'Approval: [{approval}] has been created.')
                approval.cancel_existing_annual_admission_permit(current_datetime.date())

                self.approval = approval
                self.save()

        elif self.proposal_type.code == PROPOSAL_TYPE_AMENDMENT:
            # When amendment application
            approval = self.approval.child_obj
            approval.current_proposal = self
            approval.issue_date = current_datetime
            approval.start_date = current_datetime.date()
            # We don't need to update expiry_date when amendment.  Also self.end_date can be None.
            approval.submitter = self.submitter
            approval.save()
        elif self.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
            # When renewal application
            approval = self.approval.child_obj
            approval.current_proposal = self
            approval.issue_date = current_datetime
            approval.start_date = current_datetime.date()
            approval.expiry_date = self.end_date
            approval.submitter = self.submitter
            approval.renewal_sent = False
            approval.expiry_notice_sent = False
            approval.renewal_count += 1
            approval.save()

        # update proposed_issuance_approval and MooringOnApproval if not system reissue (no request) or auto_approve
        existing_mooring_count = None
        if request and not self.auto_approve:
            # Create MooringOnApproval records
            ## also see logic in approval.add_mooring()
            mooring_id_pk = self.proposed_issuance_approval.get('mooring_id')
            ria_selected_mooring = None
            if mooring_id_pk:
                ria_selected_mooring = Mooring.objects.get(id=mooring_id_pk)

            if ria_selected_mooring:
                approval.add_mooring(mooring=ria_selected_mooring, site_licensee=False)
            else:
                if approval.current_proposal.mooring:
                    approval.add_mooring(mooring=approval.current_proposal.mooring, site_licensee=True)
            # updating checkboxes
            for moa1 in self.proposed_issuance_approval.get('mooring_on_approval'):
                for moa2 in self.approval.mooringonapproval_set.filter(mooring__mooring_licence__status='current'):
                    # convert proposed_issuance_approval to an end_date
                    if moa1.get("id") == moa2.id and not moa1.get("checked") and not moa2.end_date:
                        moa2.end_date = current_datetime.date()
                        moa2.save()
                    elif moa1.get("id") == moa2.id and moa1.get("checked") and moa2.end_date:
                        moa2.end_date = None
                        moa2.save()
        # set auto_approve renewal application ProposalRequirement due dates to those from previous application + 12 months
        if self.auto_approve and self.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
            for req in self.requirements.filter(is_deleted=False):
                if req.copied_from and req.copied_from.due_date:
                    req.due_date = req.copied_from.due_date + relativedelta(months=+12)
                    req.save()
        # do not process compliances for system reissue
        if request:
            # Generate compliances
            from mooringlicensing.components.compliances.models import Compliance, ComplianceUserAction
            target_proposal = self.previous_application if self.proposal_type.code == PROPOSAL_TYPE_AMENDMENT else self.proposal
            for compliance in Compliance.objects.filter(
                approval=approval.approval,
                proposal=target_proposal,
                processing_status='future',
                ):
                #approval_compliances.delete()
                compliance.processing_status='discarded'
                compliance.customer_status = 'discarded'
                compliance.reminder_sent=True
                compliance.post_reminder_sent=True
                compliance.save()
            self.generate_compliances(approval, request)

        # always reset this flag
        approval.renewal_sent = False
        approval.export_to_mooring_booking = True
        approval.save()

        # set proposal status to approved - can change later after manage_stickers
        self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
        self.save()

        # Retrieve newely added moorings, and send authorised user summary doc to the licence holder
        mls_to_be_emailed = []
        from mooringlicensing.components.approvals.models import MooringOnApproval, MooringLicence, Approval, Sticker
        new_moas = MooringOnApproval.objects.filter(approval=approval, sticker__isnull=True, end_date__isnull=True)  # New moa doesn't have stickers.
        if self.mooring_authorisation_preference == 'ria':
            # Do we send an authorised user mooring summary doc to someone?
            pass
        else:
            for new_moa in new_moas:
                mls_to_be_emailed = MooringLicence.objects.filter(mooring=new_moa.mooring, status__in=[Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,])

        # manage stickers
        moas_to_be_reallocated, stickers_to_be_returned = approval.manage_stickers(self)

        #####
        # Set proposal status after manage _stickers
        #####
        stickers_to_be_printed = []
        if self.approval:
            stickers_to_be_printed = self.approval.stickers.filter(status__in=[Sticker.STICKER_STATUS_NOT_READY_YET, Sticker.STICKER_STATUS_READY, Sticker.STICKER_STATUS_AWAITING_PRINTING,])

        if len(stickers_to_be_returned):
            a_sticker = stickers_to_be_returned[0]  # All the stickers to be returned should have the same vessel, so just pick the first one
            if self.vessel_ownership and a_sticker.vessel_ownership.vessel.rego_no == self.vessel_ownership.vessel.rego_no:
                # Same vessel
                if stickers_to_be_printed:
                    self.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
                    self.log_user_action(ProposalUserAction.ACTION_PRINTING_STICKER.format(self.lodgement_number), )
                else:
                    self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
            else:
                # Vessel changed OR null vessel
                # there is a sticker to be returned, application status gets 'Sticker to be Returned' status
                self.processing_status = Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED
                self.log_user_action(ProposalUserAction.ACTION_STICKER_TO_BE_RETURNED.format(self.lodgement_number), request)
        else:
            # There are no stickers to be returned
            if stickers_to_be_printed:
                # There is a sticker to be printed
                self.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
                self.log_user_action(ProposalUserAction.ACTION_PRINTING_STICKER.format(self.lodgement_number),)
            else:
                # There are no stickers to be printed
                self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
                self.log_user_action(ProposalUserAction.ACTION_PRINTING_STICKER.format(self.lodgement_number),)
        self.save()
        # self.refresh_from_db()
        # self.proposal.refresh_from_db()
        self.proposal.save()

        approval.generate_doc()
        self.proposal.refresh()  # so that the approval doc field is updated by the doc generated above

        # Email
        # send_aua_approved_or_declined_email_new_renewal(self, 'approved_paid', request, stickers_to_be_returned)
        send_application_approved_or_declined_email(self, 'approved_paid', request, stickers_to_be_returned)

        # Email to ML holder when new moorings added
        for mooring_licence in mls_to_be_emailed:
            mooring_licence.generate_au_summary_doc(request.user)
            send_au_summary_to_ml_holder(mooring_licence, request, self)

        # Log proposal action
        if self.auto_approve or not request:
            self.log_user_action(ProposalUserAction.ACTION_AUTO_APPROVED.format(self.id))
        else:
            # When get here without request, there should be already an action log for ACTION_APROVE_APPLICATION
            pass
        #     self.log_user_action(ProposalUserAction.ACTION_APPROVE_APPLICATION.format(self.id), request)

        # Write approval history
        if self.approval and self.approval.reissued:
            if request:
                approval.write_approval_history('Reissue via application {}'.format(self.lodgement_number))
        elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_RENEWAL):
            approval.write_approval_history('Renewal application {}'.format(self.lodgement_number))
        elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_AMENDMENT):
            approval.write_approval_history('Amendment application {}'.format(self.lodgement_number))
        else:
            approval.write_approval_history()

        #if existing_mooring_count and approval.mooringonapproval_set.count() > existing_mooring_count:
        #    approval.write_approval_history('mooring_add')
        #elif created:
        #    approval.write_approval_history('new')
        #else:
        #    approval.write_approval_history()
        self.save()
        self.proposal.save()

        return approval, approval_created

    @property
    def does_accept_null_vessel(self):
        # if self.proposal_type.code in (PROPOSAL_TYPE_RENEWAL,):
        #     return True
        return False

    def does_have_valid_associations(self):
        """
        Check if this application has valid associations with other applications and approvals
        """
        # TODO: implement logic
        return True


class MooringLicenceApplication(Proposal):
    REASON_FOR_EXPIRY_NOT_SUBMITTED = 'not_submitted'
    REASON_FOR_EXPIRY_NO_DOCUMENTS = 'no_documents'

    proposal = models.OneToOneField(Proposal, parent_link=True, on_delete=models.CASCADE)
    code = 'mla'
    prefix = 'ML'
    new_application_text = ""
    apply_page_visibility = False
    description = 'Mooring Site Licence Application'

    # This uuid is used to generate the URL for the ML document upload page
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)

    def reinstate_wl_allocation(self, request):
        wlallocation = self.waiting_list_allocation.reinstate_wla_order()
        self.log_user_action(f'Reinstate Waiting List Alocation: {wlallocation.lodgement_number} back to the waiting list queue.', request)
        return wlallocation

    def validate_against_existing_proposals_and_approvals(self):
        from mooringlicensing.components.approvals.models import Approval, ApprovalHistory, WaitingListAllocation, MooringLicence
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        # Get blocking proposals
        proposals = Proposal.objects.filter(
            vessel_details__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(id=self.id)
        child_proposals = [proposal.child_obj for proposal in proposals]
        proposals_mla = []
        for proposal in child_proposals:
            if proposal.processing_status not in [
                Proposal.PROCESSING_STATUS_APPROVED,
                Proposal.PROCESSING_STATUS_DECLINED,
                Proposal.PROCESSING_STATUS_DISCARDED,
            ]:
                if type(proposal) == MooringLicenceApplication:
                    proposals_mla.append(proposal)

        # Get blocking approvals
        approval_histories = ApprovalHistory.objects.filter(
            end_date=None,
            vessel_ownership__vessel=self.vessel_ownership.vessel,
            vessel_ownership__end_date__gt=today,  # Vessel has not been sold yet
        ).exclude(approval_id=self.approval_id)
        approvals = [ah.approval for ah in approval_histories]
        approvals = list(dict.fromkeys(approvals))  # remove duplicates
        approvals_ml = []
        for approval in approvals:
            if approval.status in Approval.APPROVED_STATUSES:
                if type(approval.child_obj) == MooringLicence:
                    approvals_ml.append(approval)

        if proposals_mla or approvals_ml:
            raise serializers.ValidationError("The vessel in the application is already listed in " +  
                ", ".join(['{} {} '.format(proposal.description, proposal.lodgement_number) for proposal in proposals_mla]) +
                ", ".join(['{} {} '.format(approval.description, approval.lodgement_number) for approval in approvals_ml])
            )

    def validate_vessel_length(self, request):
        min_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINIMUM_VESSEL_LENGTH).value
        min_vessel_size = float(min_vessel_size_str)
        min_mooring_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINUMUM_MOORING_VESSEL_LENGTH).value
        min_mooring_vessel_size = float(min_mooring_vessel_size_str)

        if self.proposal_type.code in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT]:
            # Even when renewal/amendment, there might not be the vessels on the approval because of the sales of the vessels.  Check it.
            min_vessel_applicable_length = self.approval.child_obj.get_current_min_vessel_applicable_length()

            if min_vessel_applicable_length > min_mooring_vessel_size:
                # There is already a sufficient size vessel for ML.
                if self.vessel_details.vessel_applicable_length < min_vessel_size:
                    logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_vessel_size_str))
                    raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_vessel_size_str))
            else:
                # There are no sufficient size vessels for ML.
                if self.vessel_details.vessel_applicable_length < min_mooring_vessel_size:
                    logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_mooring_vessel_size_str))
                    raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_mooring_vessel_size))
            if self.vessel_details.vessel_applicable_length > self.approval.child_obj.mooring.vessel_size_limit or self.vessel_details.vessel_draft > self.approval.child_obj.mooring.vessel_draft_limit:
                # Vessel is too long / Vessel draft is too deep
                logger.error("Proposal {}: Vessel unsuitable for mooring".format(self))
                raise serializers.ValidationError("Vessel unsuitable for mooring")
        elif self.vessel_details.vessel_applicable_length < min_mooring_vessel_size:
            logger.error("Proposal {}: Vessel must be at least {}m in length".format(self, min_mooring_vessel_size_str))
            raise serializers.ValidationError("Vessel must be at least {}m in length".format(min_mooring_vessel_size_str))

    def process_after_discarded(self):
        if self.waiting_list_allocation:
            self.waiting_list_allocation.process_after_discarded()

    def process_after_withdrawn(self):
        if self.waiting_list_allocation:
            self.waiting_list_allocation.process_after_withdrawn()

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    @staticmethod
    def get_intermediate_proposals(email_user_id):
        proposals = MooringLicenceApplication.objects.filter(submitter=email_user_id).exclude(processing_status__in=[
            Proposal.PROCESSING_STATUS_APPROVED,
            Proposal.PROCESSING_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_DISCARDED,])
        return proposals

    def create_fee_lines(self):
        """ Create the ledger lines - line item for application fee sent to payment system """
        logger.info(f'Creating fee lines for the MooringLicenceApplication: [{self}]...')

        from mooringlicensing.components.payments_ml.models import FeeConstructor
        from mooringlicensing.components.payments_ml.utils import generate_line_item

        accept_null_vessel = False
        current_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        target_date = self.get_target_date(current_datetime.date())
        annual_admission_type = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)  # Used for AUA / MLA

        if self.proposal_type.code == PROPOSAL_TYPE_SWAP_MOORINGS:
            total_amount = float(GlobalSettings.objects.get(key=GlobalSettings.KEY_FEE_AMOUNT_OF_SWAP_MOORINGS).value)
            incur_gst = True if GlobalSettings.objects.get(key=GlobalSettings.KEY_SWAP_MOORINGS_INCLUDES_GST).value.lower() in ['true', 't', 'yes', 'y'] else False
            if settings.DEBUG:
                # In debug environment, we want to avoid decimal number which may cuase some kind of error.
                total_amount = math.ceil(total_amount)
                total_amount_excl_tax = math.ceil(calculate_excl_gst(total_amount)) if incur_gst else math.ceil(total_amount)
            else:
                total_amount_excl_tax = calculate_excl_gst(total_amount) if incur_gst else total_amount

            # When this proposal is for Swap-Moorings, it's easy.
            return [{
                'ledger_description': 'Mooring Swap',
                'oracle_code': self.application_type.get_oracle_code_by_date(target_date),
                'price_incl_tax': total_amount, 
                'price_excl_tax': total_amount_excl_tax,
                'quantity': 1,
            }], []

        logger.info('Creating fee lines for the proposal: [{}], target date: {}'.format(self.lodgement_number, target_date))

        # Retrieve FeeItem object from FeeConstructor object
        fee_constructor_for_ml = FeeConstructor.get_fee_constructor_by_application_type_and_date(self.application_type, target_date)
        fee_constructor_for_aa = FeeConstructor.get_fee_constructor_by_application_type_and_date(annual_admission_type, target_date)

        logger.info(f'FeeConstructor (for main component(ML)): [{fee_constructor_for_ml}] has been retrieved for calculation.')
        logger.info(f'FeeConstructor (for AA component): [{fee_constructor_for_aa}] has been retrieved for calculation.')

        vessel_detais_list_to_be_processed = [self.vessel_details,]
        vessel_details_largest = self.vessel_details  # As a default value
        if self.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
            # Only when 'Renewal' application, we are interested in the existing vessels
            vessel_list = self.approval.child_obj.vessel_list_for_payment
            for vessel in vessel_list:
                if vessel != self.vessel_details.vessel:
                    vessel_detais_list_to_be_processed.append(vessel.latest_vessel_details)
                    if vessel_details_largest.vessel_applicable_length < vessel.latest_vessel_details.vessel_applicable_length:
                        vessel_details_largest = vessel.latest_vessel_details

        line_items = []  # Store all the line items
        fee_items_to_store = []  # Store all the fee_items

        logger.info(f'Largest vessel details are [{vessel_details_largest}] for the application: [{self}].')

        # For Mooring Licence component
        if vessel_details_largest:
            vessel_length = vessel_details_largest.vessel_applicable_length
        else:
            # No vessel specified in the application
            if self.does_accept_null_vessel:
                # For the amendment application or the renewal application, vessel field can be blank when submit.
                vessel_length = -1
                accept_null_vessel = True
            else:
                # msg = 'No vessel specified for the application {}'.format(self.lodgement_number)
                msg = 'The application fee admin data has not been set up correctly for the Mooring Site Licence application type.  Please contact the Rottnest Island Authority.'
                logger.error(msg)
                raise Exception(msg)

        logger.info(f'The largest size of vessels to be considered here is [{vessel_length}]m for the proposal: [{self}].')

        # Retrieve amounts paid
        max_amount_paid = self.get_max_amount_paid_for_main_component()
        logger.info(f'Max amount paid so far (for main component(ML)): ${max_amount_paid}')
        fee_item = fee_constructor_for_ml.get_fee_item(vessel_length, self.proposal_type, target_date, accept_null_vessel=accept_null_vessel)
        logger.info(f'FeeItem (for main component(ML)): [{fee_item}] has been retrieved for calculation.')
        fee_amount_adjusted = self.get_fee_amount_adjusted(fee_item, vessel_length, max_amount_paid)
        logger.info(f'Fee amount adjusted (for main component(ML)) to be paid: ${fee_amount_adjusted}')

        fee_items_to_store.append({
            'fee_item_id': fee_item.id,
            'vessel_details_id': vessel_details_largest.id if vessel_details_largest else '',
            'fee_amount_adjusted': str(fee_amount_adjusted),
        })
        line_items.append(generate_line_item(self.application_type, fee_amount_adjusted, fee_constructor_for_ml, self, current_datetime))

        # For Annual Admission component
        for vessel_details in vessel_detais_list_to_be_processed:
            # Annual admission fee is applied to each vessel.
            if not vessel_details:
                continue  # When the application was submitted with null-vessel and there are no existing vessels, process reaches this line.
            vessel_length = vessel_details.vessel_applicable_length
            max_amount_paid = self.get_max_amount_paid_for_aa_component(target_date, vessel_details.vessel)
            logger.info(f'Max amount paid so far (for AA component): ${max_amount_paid}')
            # Check if there is already an AA component paid for this vessel
            fee_item_for_aa = fee_constructor_for_aa.get_fee_item(vessel_length, self.proposal_type, target_date)
            logger.info(f'FeeItem (for AA component): [{fee_item_for_aa}] has been retrieved for calculation.')
            fee_amount_adjusted_additional = self.get_fee_amount_adjusted(fee_item_for_aa, vessel_length, max_amount_paid)
            logger.info(f'Fee amount adjusted (for AA component): ${fee_amount_adjusted_additional}')

            fee_items_to_store.append({
                'fee_item_id': fee_item_for_aa.id,
                'vessel_details_id': vessel_details.id if vessel_details else '',
                'fee_amount_adjusted': str(fee_amount_adjusted_additional),
            })
            line_items.append(generate_line_item(annual_admission_type, fee_amount_adjusted_additional, fee_constructor_for_aa, self, current_datetime, vessel_details.vessel.rego_no))

        logger.info(f'line_items calculated: {line_items}')

        return line_items, fee_items_to_store

    def get_document_upload_url(self, request):
        document_upload_url = request.build_absolute_uri(reverse('mla-documents-upload', kwargs={'uuid_str': self.uuid}))
        return document_upload_url

    @property
    def assessor_group(self):
        # return Group.objects.get(name="Mooring Licensing - Assessors: Mooring Licence")
        return ledger_api_client.managed_models.SystemGroup.objects.get(name=GROUP_ASSESSOR_MOORING_LICENCE)

    @property
    def approver_group(self):
        # return Group.objects.get(name="Mooring Licensing - Approvers: Mooring Licence")
        return ledger_api_client.managed_models.SystemGroup.objects.get(name=GROUP_APPROVER_MOORING_LICENCE)

    @property
    def assessor_recipients(self):
        # return [i.email for i in self.assessor_group.user_set.all()]
        emails = []
        for id in self.assessor_group.get_system_group_member_ids():
            emails.append(retrieve_email_userro(id).email)
        return emails

    @property
    def approver_recipients(self):
        # return [i.email for i in self.approver_group.user_set.all()]
        emails = []
        for id in self.approver_group.get_system_group_member_ids():
            emails.append(retrieve_email_userro(id).email)
        return emails

    def is_assessor(self, user):
        # return user in self.assessor_group.user_set.all()
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.assessor_group.get_system_group_member_ids()

    def is_approver(self, user):
        # return user in self.approver_group.user_set.all()
        if isinstance(user, EmailUserRO):
            user = user.id
        return user in self.approver_group.get_system_group_member_ids()

    def save(self, *args, **kwargs):
        super(MooringLicenceApplication, self).save(*args, **kwargs)
        if self.lodgement_number == '':
            new_lodgment_id = '{1}{0:06d}'.format(self.proposal_id, self.prefix)
            self.lodgement_number = new_lodgment_id
            self.save()
        self.proposal.refresh_from_db()

    def process_after_submit_other_documents(self, request):
        from mooringlicensing.components.approvals.models import WaitingListAllocation, AnnualAdmissionPermit, AuthorisedUserPermit, MooringLicence
        # Somehow in this function, followings update parent too as we expected as polymorphism
        self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
        if self.waiting_list_allocation:
            self.waiting_list_allocation.internal_status = WaitingListAllocation.INTERNAL_STATUS_SUBMITTED
            self.waiting_list_allocation.save()
        self.save()

        # Log actions
        self.log_user_action(ProposalUserAction.ACTION_SUBMIT_OTHER_DOCUMENTS, request)

        # Send email to assessors
        send_other_documents_submitted_notification_email(request, self)

    def send_emails_after_payment_success(self, request):
        # ret_value = send_submit_email_notification(request, self)
        # TODO: Send email (payment success, granted/printing-sticker)
        return True

    def process_after_submit(self, request):
        self.lodgement_date = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        self.log_user_action(ProposalUserAction.ACTION_LODGE_APPLICATION.format(self.lodgement_number), request)

        if self.proposal_type in (ProposalType.objects.filter(code__in=[PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT,])):
            # Renewal
            self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
            send_confirmation_email_upon_submit(request, self, False)
            send_notification_email_upon_submit_to_assessor(request, self)
        else:
            # New
            if self.amendment_requests.count():
                # Amendment request
                self.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
                send_confirmation_email_upon_submit(request, self, False)
                send_notification_email_upon_submit_to_assessor(request, self)
            else:
                self.processing_status = Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS
                send_documents_upload_for_mooring_licence_application_email(request, self)
                send_notification_email_upon_submit_to_assessor(request, self)
        self.save()
        logger.info(f'Status: [{self.processing_status}] has been set to the proposal: [{self}].')

    def update_or_create_approval(self, current_datetime, request=None):
        logger.info(f'Updating/Creating Mooring Site Licence from the application: [{self}]...')
        try:
            # renewal/amendment/reissue - associated ML must have a mooring
            if self.approval and hasattr(self.approval.child_obj, 'mooring') and self.approval.child_obj.mooring:
                existing_mooring_licence = self.approval.child_obj
            else:
                existing_mooring_licence = self.allocated_mooring.mooring_licence if self.allocated_mooring else None
            
            if self.proposal_type.code == settings.PROPOSAL_TYPE_SWAP_MOORINGS:
                # When swap moorings, self.allocated_mooring should have a target mooring.
                mooring = self.allocated_mooring
            else:
                mooring = existing_mooring_licence.mooring if existing_mooring_licence else self.allocated_mooring
            created = None
            approval_created = False

            if self.proposal_type.code in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_SWAP_MOORINGS,]:
                approval = self.approval.child_obj
                approval.current_proposal=self
                approval.issue_date = current_datetime
                approval.start_date = current_datetime.date()
                if self.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
                    # When renewal, we have to update the expiry_date of the approval
                    approval.expiry_date = self.end_date
                approval.submitter = self.submitter
                approval.save()
                if self.proposal_type.code == PROPOSAL_TYPE_SWAP_MOORINGS:
                    current_mooring = approval.mooring if hasattr(approval, 'mooring') else None
                    target_mooring = self.allocated_mooring

                    if current_mooring:
                        if current_mooring.mooring_licence == approval:
                            # Mooring and MooringLicence is a 1TO1 relation.  To prevent it from having 1TOMany relation, set None to both moorings.
                            current_mooring.mooring_licence = None
                            current_mooring.save()
                            logger.info(f'Remove the link between the MSL: [{approval}] and the mooring: [{current_mooring}].')
                            current_mooring.handle_aups_after_save_mooring(request)

                            temp_licence = target_mooring.mooring_licence
                            target_mooring.mooring_licence = None
                            target_mooring.save()
                            logger.info(f'Remove the link between the MSL: [{temp_licence}] and the mooring: [{target_mooring}].')
                            target_mooring.handle_aups_after_save_mooring(request)

                    # Create new relation between the approval and the mooring
                    target_mooring.mooring_licence = approval
                    target_mooring.save()
                    logger.info(f'Create a link between the MSL: [{approval}] and the mooring: [{target_mooring}].')
            else:
                approval, approval_created = self.approval_class.objects.update_or_create(
                    current_proposal=self,
                    defaults={
                        'issue_date': current_datetime,
                        'start_date': current_datetime.date(),
                        'expiry_date': self.end_date,
                        'submitter': self.submitter,
                    }
                )

                if approval_created:
                    from mooringlicensing.components.approvals.models import Approval
                    logger.info(f'Approval: [{approval}] has been created.')
                    approval.cancel_existing_annual_admission_permit(current_datetime.date())

                # associate mooring licence with mooring, only on NEW proposal_type
                if not self.approval:
                    self.allocated_mooring.mooring_licence = approval
                    self.allocated_mooring.save()
                # always associate proposal with approval
                if approval_created:
                    self.approval = approval
                    self.save()
                # Move WLA to status approved
                if self.waiting_list_allocation:
                    self.waiting_list_allocation.process_after_approval()

            # update proposed_issuance_approval and MooringOnApproval if not system reissue (no request) or auto_approve
            if request and not self.auto_approve:
                # Create VesselOwnershipOnApproval records
                ## also see logic in approval.add_vessel_ownership()
                vooa, created = approval.add_vessel_ownership(vessel_ownership=self.vessel_ownership)

                # updating checkboxes
                #if self.approval:
                for vo1 in self.proposed_issuance_approval.get('vessel_ownership'):
                    for vo2 in self.approval.vesselownershiponapproval_set.all():
                        # convert proposed_issuance_approval to an end_date
                        if vo1.get("id") == vo2.vessel_ownership.id and not vo1.get("checked") and not vo2.end_date:
                            vo2.end_date = current_datetime.date()
                            vo2.save()
                        elif vo1.get("id") == vo2.vessel_ownership.id and vo1.get("checked") and vo2.end_date:
                            vo2.end_date = None
                            vo2.save()
            # set auto_approve renewal application ProposalRequirement due dates to those from previous application + 12 months
            if self.auto_approve and self.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
                for req in self.requirements.filter(is_deleted=False):
                    if req.copied_from and req.copied_from.due_date:
                        req.due_date = req.copied_from.due_date + relativedelta(months=+12)
                        req.save()
            # do not process compliances for system reissue
            if request:
                # Generate compliances
                from mooringlicensing.components.compliances.models import Compliance, ComplianceUserAction
                #if self.proposal_type == PROPOSAL_TYPE_AMENDMENT:
                #target_proposal = self.previous_application if self.previous_application else self.proposal
                target_proposal = self.previous_application if self.proposal_type.code == PROPOSAL_TYPE_AMENDMENT else self.proposal
                for compliance in Compliance.objects.filter(
                    approval=approval.approval,
                    proposal=target_proposal,
                    processing_status='future',
                    ):
                #approval_compliances.delete()
                    compliance.processing_status = Compliance.PROCESSING_STATUS_DISCARDED
                    compliance.customer_status = Compliance.CUSTOMER_STATUS_DISCARDED
                    compliance.reminder_sent=True
                    compliance.post_reminder_sent=True
                    compliance.save()
                self.generate_compliances(approval, request)

            mooring.log_user_action(
                MooringUserAction.ACTION_ASSIGN_MOORING_LICENCE.format(
                    str(approval),
                ),
                request
            )
            # always reset this flag
            approval.renewal_sent = False
            approval.export_to_mooring_booking = True
            approval.save()

            # set proposal status to approved - can change later after manage_stickers
            self.processing_status = Proposal.PROCESSING_STATUS_APPROVED
            self.save()

            # manage stickers
            moas_to_be_reallocated, stickers_to_be_returned = approval.manage_stickers(self)

            # Creating documents should be performed at the end
            approval.generate_doc()
            if self.proposal_type.code in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_SWAP_MOORINGS,]:
                approval.generate_au_summary_doc(request.user)

            # Email with attachments
            # send_mla_approved_or_declined_email_new_renewal(self, 'approved_paid', request, stickers_to_be_returned)
            send_application_approved_or_declined_email(self, 'approved_paid', request, stickers_to_be_returned)

            # Log proposal action
            if self.auto_approve or not request:
                self.log_user_action(ProposalUserAction.ACTION_AUTO_APPROVED.format(self.id))
            else:
                # When get here without request, there should be already an action log for ACTION_APROVE_APPLICATION
                pass
            #     self.log_user_action(ProposalUserAction.ACTION_APPROVE_APPLICATION.format(self.id), request)

            # write approval history
            if self.approval and self.approval.reissued:
                approval.write_approval_history('Reissue via application {}'.format(self.lodgement_number))
            elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_RENEWAL):
                approval.write_approval_history('Renewal application {}'.format(self.lodgement_number))
            elif self.proposal_type == ProposalType.objects.get(code=PROPOSAL_TYPE_AMENDMENT):
                approval.write_approval_history('Amendment application {}'.format(self.lodgement_number))
            else:
                approval.write_approval_history()

            return approval, approval_created
        except Exception as e:
            print(e)
            msg = 'Payment taken for Proposal: {}, but approval creation has failed\n{}'.format(self.lodgement_number, str(e))
            logger.error(msg)
            logger.error(traceback.print_exc())
            raise e

    @property
    def does_accept_null_vessel(self):
        if self.proposal_type.code in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_SWAP_MOORINGS,]:
            return True
        return False

    def does_have_valid_associations(self):
        """
        Check if this application has valid associations with other applications and approvals
        """
        # TODO: implement logic
        return True


class ProposalLogDocument(Document):
    log_entry = models.ForeignKey('ProposalLogEntry',related_name='documents', on_delete=models.CASCADE)
    _file = models.FileField(storage=private_storage,upload_to=update_proposal_comms_log_filename, max_length=512)

    class Meta:
        app_label = 'mooringlicensing'


class ProposalLogEntryManager(models.Manager):
    def create(self, *args, **kwargs):
        if 'customer' in kwargs and isinstance(kwargs['customer'], EmailUserRO):
            kwargs['customer'] = kwargs['customer'].id
        if 'staff' in kwargs and isinstance(kwargs['staff'], EmailUserRO):
            kwargs['staff'] = kwargs['staff'].id
        return super(ProposalLogEntryManager, self).create(*args, **kwargs)


class ProposalLogEntry(CommunicationsLogEntry):
    proposal = models.ForeignKey(Proposal, related_name='comms_logs', on_delete=models.CASCADE)
    objects = ProposalLogEntryManager()

    def __str__(self):
        return '{} - {}'.format(self.reference, self.subject)

    class Meta:
        app_label = 'mooringlicensing'

    def save(self, **kwargs):
        # save the application reference if the reference not provided
        if not self.reference:
            if hasattr(self.proposal, 'reference'):
                self.reference = self.proposal.reference
        super(ProposalLogEntry, self).save(**kwargs)


# not for admin - data comes from Mooring Bookings
class MooringBay(RevisionedMixin):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=3, blank=True, null=True)
    mooring_bookings_id = models.IntegerField()
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Mooring Bays"
        app_label = 'mooringlicensing'


class PrivateMooringManager(models.Manager):
    def get_queryset(self):
        return super(PrivateMooringManager, self).get_queryset().filter(mooring_bookings_mooring_specification=2)


class AuthorisedUserMooringManager(models.Manager):
    def get_queryset(self):
        # from mooringlicensing.components.approvals.models import Approval
        # ret = super(AuthorisedUserMooringManager, self).get_queryset().filter(mooring_bookings_mooring_specification=2, mooring_licence__status=Approval.APPROVAL_STATUS_CURRENT)
        ret = super(AuthorisedUserMooringManager, self).get_queryset().filter(mooring_bookings_mooring_specification=2,)  # Can any mooring be an authorised user mooring, can't it?
        return ret


class AvailableMooringManager(models.Manager):
    def get_queryset(self):
        from mooringlicensing.components.approvals.models import Approval

        available_ids = []
        for mooring in Mooring.private_moorings.all():
            # first check mooring_licence status
            if not mooring.mooring_licence or mooring.mooring_licence.status != Approval.APPROVAL_STATUS_CURRENT:
                # now check whether there are any blocking proposals
                blocking_proposal = False
                for proposal in mooring.ria_generated_proposal.all():
                    if proposal.processing_status not in [Proposal.PROCESSING_STATUS_APPROVED, Proposal.PROCESSING_STATUS_DECLINED, Proposal.PROCESSING_STATUS_DISCARDED,]:
                        blocking_proposal = True
                if not blocking_proposal:
                    available_ids.append(mooring.id)

        return super(AvailableMooringManager, self).get_queryset().filter(id__in=available_ids)


# not for admin - data comes from Mooring Bookings
class Mooring(RevisionedMixin):
    MOORING_SPECIFICATION = (
         (1, 'Rental Mooring'),
         (2, 'Private Mooring'),
    )

    name = models.CharField(max_length=100)
    mooring_bay = models.ForeignKey(MooringBay, on_delete=models.CASCADE)
    active = models.BooleanField(default=True)
    vessel_size_limit = models.DecimalField(max_digits=8, decimal_places=2, default='0.00') # does not exist in MB
    vessel_draft_limit = models.DecimalField(max_digits=8, decimal_places=2, default='0.00')
    vessel_beam_limit = models.DecimalField(max_digits=8, decimal_places=2, default='0.00')
    vessel_weight_limit = models.DecimalField(max_digits=8, decimal_places=2, default='0.00') # tonnage
    # stored for debugging purposes, not used in this system
    mooring_bookings_id = models.IntegerField()
    mooring_bookings_mooring_specification = models.IntegerField(choices=MOORING_SPECIFICATION)
    mooring_bookings_bay_id = models.IntegerField()
    # model managers
    objects = models.Manager()
    private_moorings = PrivateMooringManager()
    authorised_user_moorings = AuthorisedUserMooringManager()
    available_moorings = AvailableMooringManager()
    # Used for WLAllocation create MLApplication check
    # mooring licence can onl,y have one Mooring
    mooring_licence = models.OneToOneField('MooringLicence', blank=True, null=True, related_name="mooring", on_delete=models.SET_NULL)

    def handle_aups_after_save_mooring(self, request):
        logger.debug(f'in handle_aups_after_save_mooring().  self: [{self}]')

        from mooringlicensing.components.approvals.models import Approval, MooringOnApproval

        today=datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        if not self.mooring_licence:
            # This mooring doesn't have a link to any mooring_licences
            query = Q()
            query &= Q(mooring=self)
            query &= Q(approval__status__in=[Approval.APPROVAL_STATUS_SUSPENDED, Approval.APPROVAL_STATUS_CURRENT,])
            query &= Q(Q(end_date__gt=today) | Q(end_date__isnull=True))  # No end date or future end date
            
            # Retrieve all the AUPs which link to the mooring without any MSLs.  Which means we have to set the end_date and cancell the AUPs.
            active_mooring_on_approvals = MooringOnApproval.objects.filter(query)

            for active_mooring_on_approval in active_mooring_on_approvals:
                logger.debug(f'active_mooring_on_approval: [{active_mooring_on_approval}]')

                # Set end date.
                active_mooring_on_approval.end_date = today  
                active_mooring_on_approval.save()
                logger.info(f'End date: [{today}] has been set to the MooringOnApproval: [{active_mooring_on_approval}] .')

                # Set 'to_be_returned' to the sticker
                # from mooringlicensing.components.approvals.models import Sticker
                # sticker = active_mooring_on_approval.sticker
                # if sticker:
                #     sticker.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
                #     sticker.save()
                #     # TODO: check existence of other valid moorings
                #     # This sticker may have another mooring which is still valid.  In that case a new sticker must be printed.
                # else:
                #     # Should not reach here
                #     logger.warn(f'')

                # Cancel AUP
                # affected_aup = active_mooring_on_approval.approval
                # affected_aup.approval_cancellation(
                #     request, 
                #     {
                #         'cancellation_date': today,
                #         'cancellation_details': 'The licence has been cancelled due to a mooring swap.',
                #         'cancel_due_to_mooring_swap': True,
                #         'mooring': self,
                #         'sticker': sticker,
                #     }
                # )

                active_mooring_on_approval.approval.manage_stickers()  
                active_mooring_on_approval.approval.generate_doc()
                send_aup_revoked_due_to_mooring_swap_email(request, active_mooring_on_approval.approval.child_obj, active_mooring_on_approval.mooring, [active_mooring_on_approval.sticker,])


    def __str__(self):
        return f'{self.name} (Bay: {self.mooring_bay.name})'

    class Meta:
        verbose_name_plural = "Moorings"
        app_label = 'mooringlicensing'

    @property
    def specification_display(self):
        return self.get_mooring_bookings_mooring_specification_display()

    def log_user_action(self, action, request):
        return MooringUserAction.log_action(self, action, request.user.id)

    @property
    def status(self):
        from mooringlicensing.components.approvals.models import MooringOnApproval
        status = ''
        ## check for Mooring Licences
        if self.mooring_licence and self.mooring_licence.status in ['current', 'suspended']:
            status = 'Licensed'
        if not status:
            # check for Mooring Applications
            proposals = self.ria_generated_proposal.exclude(processing_status__in=['approved', 'declined', 'discarded'])
            for proposal in proposals:
                if proposal.child_obj.code == 'mla':
                    status = 'Licence Application'
        return status if status else 'Unlicensed'

    #def suitable_vessel(self, vessel_details=None):
    def suitable_vessel(self, vessel_details):
        suitable = True
        #if not vessel_details:
         #   suitable = ''
        if vessel_details.vessel_applicable_length > self.vessel_size_limit or vessel_details.vessel_draft > self.vessel_draft_limit:
            suitable = False
        return suitable

class MooringLogDocument(Document):
    log_entry = models.ForeignKey('MooringLogEntry',related_name='documents', on_delete=models.CASCADE)
    _file = models.FileField(storage=private_storage,upload_to=update_mooring_comms_log_filename, max_length=512)

    class Meta:
        app_label = 'mooringlicensing'


class MooringLogEntry(CommunicationsLogEntry):
    mooring = models.ForeignKey(Mooring, related_name='comms_logs', on_delete=models.CASCADE)

    def __str__(self):
        return '{} - {}'.format(self.reference, self.subject)

    class Meta:
        app_label = 'mooringlicensing'

class MooringUserAction(UserAction):
    ACTION_ASSIGN_MOORING_LICENCE = "Assign Mooring Site Licence {}"
    ACTION_SWITCH_MOORING_LICENCE = "Remove existing Mooring Site Licence {} and assign {}"

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ('-when',)

    @classmethod
    def log_action(cls, mooring, action, user):
        if isinstance(user, EmailUserRO):
            user = user.id
        return cls.objects.create(
            mooring=mooring,
            who=user if user else None,
            what=str(action)
        )

    mooring = models.ForeignKey(Mooring, related_name='action_logs', on_delete=models.CASCADE)


class Vessel(RevisionedMixin):
    rego_no = models.CharField(max_length=200, unique=True, blank=False, null=False)
    blocking_owner = models.ForeignKey('VesselOwnership', blank=True, null=True, related_name='blocked_vessel', on_delete=models.SET_NULL)

    class Meta:
        verbose_name_plural = "Vessels"
        app_label = 'mooringlicensing'

    def __str__(self):
        return self.rego_no

    def get_current_wlas(self, target_date):
        from mooringlicensing.components.approvals.models import Approval, WaitingListAllocation
        existing_wlas = WaitingListAllocation.objects.filter(
            status__in=(Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,),
            start_date__lte=target_date,
            expiry_date__gte=target_date,
            current_proposal__vessel_details__vessel=self,
            current_proposal__vessel_ownership__end_date__isnull=True,
        ).distinct()
        return existing_wlas

    def get_current_aaps(self, target_date):
        from mooringlicensing.components.approvals.models import Approval, AnnualAdmissionPermit
        existing_aaps = AnnualAdmissionPermit.objects.filter(
            status__in=(Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,),
            start_date__lte=target_date,
            expiry_date__gte=target_date,
            current_proposal__vessel_details__vessel=self,
            current_proposal__vessel_ownership__end_date__isnull=True,
        ).distinct()
        return existing_aaps

    def get_current_aups(self, target_date):
        from mooringlicensing.components.approvals.models import Approval, AuthorisedUserPermit
        existing_aups = AuthorisedUserPermit.objects.filter(
            status__in=(Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,),
            start_date__lte=target_date,
            expiry_date__gte=target_date,
            current_proposal__vessel_details__vessel=self,
            current_proposal__vessel_ownership__end_date__isnull=True,
        ).distinct()
        return existing_aups

    def get_current_mls(self, target_date):
        from mooringlicensing.components.approvals.models import Approval, MooringLicence
        existing_mls = MooringLicence.objects.filter(
            status__in=(Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,),
            start_date__lte=target_date,
            expiry_date__gte=target_date,
            proposal__processing_status__in=(Proposal.PROCESSING_STATUS_PRINTING_STICKER, Proposal.PROCESSING_STATUS_APPROVED,),
            proposal__vessel_details__vessel=self,
            proposal__vessel_ownership__end_date__isnull=True,
        ).distinct()
        return existing_mls

    def get_current_approvals(self, target_date):
        # Return all the approvals where this vessel is on.
        existing_wla = self.get_current_wlas(target_date)
        existing_aaps = self.get_current_aaps(target_date)
        existing_aups = self.get_current_aups(target_date)
        existing_mls = self.get_current_mls(target_date)

        return {
            'wla': existing_wla,
            'aaps': existing_aaps,
            'aups': existing_aups,
            'mls': existing_mls
        }

    ## at submit
    def check_blocking_ownership(self, vessel_ownership, proposal_being_processed):
        logger.info(f'Checking blocking ownership for the proposal: [{proposal_being_processed}]...')
        from mooringlicensing.components.approvals.models import Approval, MooringLicence

        ## Requirement: If vessel is owned by multiple parties then there must be no
        # 1. other application in status other than issued, declined or discarded where the applicant is another owner than this applicant
        proposals_filter = Q()  # This is condition for the proposal to be blocking proposal.
        proposals_filter &= Q(vessel_ownership__vessel=self)  # Blocking proposal is for the same vessel
        proposals_filter &= ~Q(processing_status__in=[  # Blocking proposal's status is not the statuses listed
            Proposal.PROCESSING_STATUS_APPROVED,
            Proposal.PROCESSING_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_EXPIRED,
            Proposal.PROCESSING_STATUS_DISCARDED,
        ])
        proposals_filter &= ~Q(id=proposal_being_processed.id)  # Blocking proposal is not the proposal being processed, of course

        blocking_proposals = Proposal.objects.filter(proposals_filter)
        for bp in blocking_proposals:
            logger.debug(f'blocking_proposal: [{bp}]')

        if blocking_proposals:
            logger.info(f'Blocking proposal(s): [{blocking_proposals}] found.  This vessel: [{self}] is already listed with RIA under another owner.')
            raise serializers.ValidationError("This vessel is already listed with RIA under another owner")

        # 2. Annual Admission Permit, Authorised User Permit or Mooring Licence in status other than expired, cancelled, or surrendered
        #    where Permit or Licence holder is an owner other than the applicant of this Waiting List application
        ## ML Filter <== Is this just for WLApplication...???
        ml_filter = Q(
            ~Q(
                Q(status__in=[
                    Approval.APPROVAL_STATUS_CANCELLED,
                    Approval.APPROVAL_STATUS_EXPIRED,
                    Approval.APPROVAL_STATUS_SURRENDERED]) |
                Q(proposal__vessel_ownership=vessel_ownership) |
                Q(proposal=proposal_being_processed)
            ) &
            Q(vesselownershiponapproval__approval__current_proposal__processing_status__in=[
                Proposal.PROCESSING_STATUS_PRINTING_STICKER,
                Proposal.PROCESSING_STATUS_APPROVED]) &
            Q(vesselownershiponapproval__vessel_ownership__end_date__isnull=True) &
            Q(vesselownershiponapproval__end_date__isnull=True) &
            Q(vesselownershiponapproval__vessel_ownership__vessel=self)
        )
        blocking_approvals = MooringLicence.objects.filter(ml_filter)
        if blocking_approvals:
            logger.info(f'Blocking approval(s): [{blocking_approvals}] found.  Another owner of this vessel: [{self}] holds a current Mooring Site Licence.')
            raise serializers.ValidationError("Another owner of this vessel holds a current Mooring Site Licence")

        ## 3. Other Approvals filter
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()
        approval_filter = Q()
        # Approval is for the same vessel
        approval_filter &= Q(current_proposal__vessel_ownership__vessel=self)
        # The vessel has not been sold yet
        approval_filter &= Q(current_proposal__vessel_ownership__end_date__gt=today)
        # Approval holder is different person
        approval_filter &= ~Q(current_proposal__vessel_ownership=vessel_ownership)
        # We don't want to include the approval this proposal is for.
        approval_filter &= ~Q(id=proposal_being_processed.approval.id) if proposal_being_processed.approval else Q()

        blocking_approvals = Approval.objects.filter(approval_filter)
        if blocking_approvals:
            logger.info(f'Blocking approval(s): [{blocking_approvals}] found.  Another owner of this vessel: [{self}] holds a current Licence/Permit.')
            raise serializers.ValidationError("Another owner of this vessel holds a current Licence/Permit")

    @property
    def latest_vessel_details(self):
        return self.filtered_vesseldetails_set.first()

    @property
    def filtered_vesselownership_set(self):
        return self.vesselownership_set.filter(
                id__in=VesselOwnership.filtered_objects.values_list('id', flat=True)
                )

    @property
    def filtered_vesseldetails_set(self):
        return self.vesseldetails_set.filter(
                id__in=VesselDetails.filtered_objects.values_list('id', flat=True)
                )


class VesselLogDocument(Document):
    log_entry = models.ForeignKey('VesselLogEntry',related_name='documents', on_delete=models.CASCADE)
    _file = models.FileField(storage=private_storage,upload_to=update_vessel_comms_log_filename, max_length=512)

    class Meta:
        app_label = 'mooringlicensing'


class VesselLogEntry(CommunicationsLogEntry):
    vessel = models.ForeignKey(Vessel, related_name='comms_logs', on_delete=models.CASCADE)

    def __str__(self):
        return '{} - {}'.format(self.reference, self.subject)

    class Meta:
        app_label = 'mooringlicensing'

class VesselDetailsManager(models.Manager):
    def get_queryset(self):
        latest_ids = VesselDetails.objects.values("vessel").annotate(id=Max('id')).values_list('id', flat=True)
        return super(VesselDetailsManager, self).get_queryset().filter(id__in=latest_ids)


class VesselDetails(RevisionedMixin): # ManyToManyField link in Proposal
    vessel_type = models.CharField(max_length=20, choices=VESSEL_TYPES)
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)
    vessel_name = models.CharField(max_length=400)
    vessel_length = models.DecimalField(max_digits=8, decimal_places=2, default='0.00') # does not exist in MB
    vessel_draft = models.DecimalField(max_digits=8, decimal_places=2)
    vessel_beam = models.DecimalField(max_digits=8, decimal_places=2, default='0.00')
    vessel_weight = models.DecimalField(max_digits=8, decimal_places=2) # tonnage
    berth_mooring = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(auto_now=True)
    # for cron job
    exported = models.BooleanField(default=False) # must be False after every add/edit
    objects = models.Manager()
    filtered_objects = VesselDetailsManager()

    class Meta:
        verbose_name_plural = "Vessel Details"
        app_label = 'mooringlicensing'

    def __str__(self):
        return "{}".format(self.id)

    @property
    def vessel_applicable_length(self):
        return float(self.vessel_length)


class CompanyOwnership(RevisionedMixin):
    # Possible status flow
    # 'draft' --> 'approved' --> 'old'
    #         \-> 'declined'
    COMPANY_OWNERSHIP_STATUS_APPROVED = 'approved'
    COMPANY_OWNERSHIP_STATUS_DRAFT = 'draft'
    COMPANY_OWNERSHIP_STATUS_OLD = 'old'
    COMPANY_OWNERSHIP_STATUS_DECLINED = 'declined'
    STATUS_TYPES = (
        (COMPANY_OWNERSHIP_STATUS_APPROVED, 'Approved'),
        (COMPANY_OWNERSHIP_STATUS_DRAFT, 'Draft'),
        (COMPANY_OWNERSHIP_STATUS_OLD, 'Old'),
        (COMPANY_OWNERSHIP_STATUS_DECLINED, 'Declined'),
    )
    blocking_proposal = models.ForeignKey(Proposal, blank=True, null=True, on_delete=models.SET_NULL)
    # status = models.CharField(max_length=50, choices=STATUS_TYPES, default=COMPANY_OWNERSHIP_STATUS_DRAFT) # can be approved, old, draft, declined
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)
    company = models.ForeignKey('Company', on_delete=models.CASCADE)
    percentage = models.IntegerField(null=True, blank=True)
    ## TODO: delete start and end dates if no longer required
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(auto_now=True, blank=True)

    class Meta:
        verbose_name_plural = "Company Ownership"
        app_label = 'mooringlicensing'

    def __str__(self):
        return f"{self.company}: {self.percentage}%"

    def save(self, *args, **kwargs):
        from mooringlicensing.components.approvals.models import AuthorisedUserPermit, MooringLicence
        ## do not allow multiple draft or approved status per vessel_id
        # restrict multiple draft records
        # if not self.pk:
        #     company_ownerships = CompanyOwnership.objects.filter(vessel=self.vessel, company=self.company)
        #     for company_ownership in company_ownerships:
        #         if company_ownership.status == CompanyOwnership.COMPANY_OWNERSHIP_STATUS_DRAFT:
        #             raise ValueError("Multiple draft status records for the same company/vessel combination are not allowed")
        #         elif company_ownership.status == CompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED and self.status == CompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED:
        #             raise ValueError("Multiple approved status records for the same company/vessel combination are not allowed")
        existing_record = True if CompanyOwnership.objects.filter(id=self.id) else False
        if existing_record:
            prev_end_date = CompanyOwnership.objects.get(id=self.id).end_date
        super(CompanyOwnership, self).save(*args,**kwargs)
        ## Reissue associated ML and AUPs if end-dated
        if existing_record and not prev_end_date and self.end_date:
            aup_set = AuthorisedUserPermit.objects.filter(current_proposal__vessel_ownership__company_ownership=self)
            for aup in aup_set:
                from mooringlicensing.components.approvals.models import Approval
                if aup.status == Approval.APPROVAL_STATUS_CURRENT:
                    aup.internal_reissue()
            ## ML
            vo_set = self.vesselownership_set.all()
            for vo in vo_set:
                proposal_set = vo.proposal_set.all()
                for proposal in proposal_set:
                    if proposal.approval and type(proposal.approval) == MooringLicence and proposal.approval.status == Approval.APPROVAL_STATUS_CURRENT:
                        proposal.approval.internal_reissue()


class VesselOwnershipCompanyOwnership(RevisionedMixin):
    COMPANY_OWNERSHIP_STATUS_APPROVED = 'approved'
    COMPANY_OWNERSHIP_STATUS_DRAFT = 'draft'
    COMPANY_OWNERSHIP_STATUS_OLD = 'old'
    COMPANY_OWNERSHIP_STATUS_DECLINED = 'declined'
    STATUS_TYPES = (
        (COMPANY_OWNERSHIP_STATUS_APPROVED, 'Approved'),
        (COMPANY_OWNERSHIP_STATUS_DRAFT, 'Draft'),
        (COMPANY_OWNERSHIP_STATUS_OLD, 'Old'),
        (COMPANY_OWNERSHIP_STATUS_DECLINED, 'Declined'),
    )
    vessel_ownership = models.ForeignKey('VesselOwnership', null=True, on_delete=models.SET_NULL)
    company_ownership = models.ForeignKey(CompanyOwnership, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=50, choices=STATUS_TYPES, default=COMPANY_OWNERSHIP_STATUS_DRAFT) # can be approved, old, draft, declined

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        return f'id:{self.id}, vessel_ownership: [{self.vessel_ownership}], company_ownership: [{self.company_ownership}], status: [{self.status}]'


class VesselOwnershipManager(models.Manager):
    def get_queryset(self):
        #latest_ids = VesselOwnership.objects.values("owner", "vessel", "company_ownership").annotate(id=Max('id')).values_list('id', flat=True)
        # Do not show sold vessels
        latest_ids = VesselOwnership.objects.filter(end_date__isnull=True).values("owner", "vessel", "company_ownerships").annotate(id=Max('id')).values_list('id', flat=True)
        return super(VesselOwnershipManager, self).get_queryset().filter(id__in=latest_ids)


class VesselOwnership(RevisionedMixin):
    owner = models.ForeignKey('Owner', on_delete=models.CASCADE)
    vessel = models.ForeignKey(Vessel, on_delete=models.CASCADE)
    percentage = models.IntegerField(null=True, blank=True)
    start_date = models.DateTimeField(default=timezone.now)
    # date of sale
    end_date = models.DateField(null=True, blank=True)
    created = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(auto_now=True)
    # for cron job
    exported = models.BooleanField(default=False) # must be False after every add/edit
    objects = models.Manager()
    filtered_objects = VesselOwnershipManager()
    ## Name as shown on DoT registration papers
    dot_name = models.CharField(max_length=200, blank=True, null=True)
    company_ownerships = models.ManyToManyField(CompanyOwnership, null=True, blank=True, related_name='vessel_ownerships', through=VesselOwnershipCompanyOwnership)

    class Meta:
        verbose_name_plural = "Vessel Ownership"
        app_label = 'mooringlicensing'

    def get_latest_company_ownership(self, status_list=[VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_DRAFT, VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED,]):
        if self.company_ownerships.count():
            company_ownership = self.company_ownerships.filter(vesselownershipcompanyownership__status__in=status_list).order_by('created').last()
            return company_ownership
            # company_ownership = self.company_ownerships.all().order_by('updated').last()
            # for voco in VesselOwnershipCompanyOwnership.objects.filter(vessel_ownership=self, company_ownership=company_ownership):
            #     if voco.status in status_list:
            #         return company_ownership
        return CompanyOwnership.objects.none()
    
    @property
    def applicable_owner_name(self):
        co = self.get_latest_company_ownership([VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED,])
        if co:
            return co.company.name
        else:
            return str(self.owner)

    @property
    def applicable_percentage(self):
        co = self.get_latest_company_ownership([VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED,])
        if co:
            return co.percentage
        else:
            return self.percentage

    @property
    def company_ownership_latest(self):
        if self.company_ownerships.count():
            return self.company_ownerships.order_by('created').last()

    @property
    def individual_owner(self):
        if self.get_latest_company_ownership():
            return False
        else:
            return True

    def __str__(self):
        return f'id:{self.id}, owner: {self.owner}, company_ownership: [{self.get_latest_company_ownership([VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED, VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_DRAFT,])}], vessel: {self.vessel}'

    def excludable(self, originated_proposal):
        # Return True if self is excludable from the percentage calculation

        def get_latest_proposals(proposal, latest_proposals_list):
            if not proposal.succeeding_proposals.count():
                if proposal not in latest_proposals_list:
                    latest_proposals_list.append(proposal)
            else:
                for succeeding_proposal in proposal.succeeding_proposals.all():
                    get_latest_proposals(succeeding_proposal, latest_proposals_list)

        excludable = True

        latest_proposals = []
        for proposal in self.proposal_set.exclude(processing_status__in=[
            Proposal.PROCESSING_STATUS_DECLINED,
            Proposal.PROCESSING_STATUS_DISCARDED,
        ]):
            get_latest_proposals(proposal, latest_proposals)

        for proposal in latest_proposals:
            if proposal == originated_proposal:
                continue
            from mooringlicensing.components.approvals.models import Approval
            if proposal.approval and proposal.approval.status in [Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,]:
                excludable = False

        return excludable

    def get_fee_items_paid(self):
        # Return all the fee_items for this vessel
        fee_items = []
        from mooringlicensing.components.approvals.models import Approval
        for proposal in self.proposal_set.filter(approval__isnull=False, approval__status__in=(Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,)):
            for item in proposal.get_fee_items_paid():
                if item not in fee_items:
                    fee_items.append(item)
        return fee_items

    def save(self, *args, **kwargs):
        from mooringlicensing.components.approvals.models import AuthorisedUserPermit, MooringLicence
        existing_record = True if VesselOwnership.objects.filter(id=self.id) else False
        if existing_record:
            prev_end_date = VesselOwnership.objects.get(id=self.id).end_date
        super(VesselOwnership, self).save(*args,**kwargs)
        ## Reissue associated ML and AUPs if end-dated
        if existing_record and not prev_end_date and self.end_date:
            aup_set = AuthorisedUserPermit.objects.filter(current_proposal__vessel_ownership=self)
            for aup in aup_set:
                if aup.status == 'current':
                    aup.internal_reissue()
            ## ML
            proposal_set = self.proposal_set.all()
            for proposal in proposal_set:
                if proposal.approval and type(proposal.approval) == MooringLicence and proposal.approval.status == 'current':
                    proposal.approval.internal_reissue()


class VesselRegistrationDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/vessel_registration_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    vessel_ownership = models.ForeignKey(VesselOwnership, null=True, blank=True, related_name='vessel_registration_documents', on_delete=models.CASCADE)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide= models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden=models.BooleanField(default=False) # after initial submit prevent document from being deleted
    # test
    proposal = models.ForeignKey(Proposal, null=True, blank=True, related_name='temp_vessel_registration_documents', on_delete=models.CASCADE)
    original_file_name = models.CharField(max_length=512, null=True, blank=True)
    original_file_ext = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Vessel Registration Papers"

    def __str__(self):
        ret_str = f'{self.original_file_name}{self.original_file_ext}'
        if self._file:
            ret_str += f' ({self._file.url})'
        return ret_str

class Owner(RevisionedMixin):
    # emailuser = models.OneToOneField(EmailUser, on_delete=models.CASCADE)
    emailuser = models.IntegerField(unique=True)  # unique=True keeps the OneToOne relation
    # add on approval only
    vessels = models.ManyToManyField(Vessel, through=VesselOwnership) # these owner/vessel association

    class Meta:
        verbose_name_plural = "Owners"
        app_label = 'mooringlicensing'

    @property
    def emailuser_obj(self):
        return retrieve_email_userro(self.emailuser)

    def __str__(self):
        if self.emailuser:
            from mooringlicensing.ledger_api_utils import retrieve_email_userro
            emailuser = retrieve_email_userro(self.emailuser)
            if emailuser:
                return emailuser.get_full_name()
            else:
                return ''
            # return emailuser.get_full_name()
        else:
            return ''
        # return self.emailuser.get_full_name()


class Company(RevisionedMixin):
    name = models.CharField(max_length=200, unique=True, blank=True, null=True)
    vessels = models.ManyToManyField(Vessel, through=CompanyOwnership) # these owner/vessel association

    class Meta:
        verbose_name_plural = "Companies"
        app_label = 'mooringlicensing'

    def __str__(self):
        return f'{self.name} (id: {self.id})'


class InsuranceCertificateDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/insurance_certificate_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    proposal = models.ForeignKey(Proposal,related_name='insurance_certificate_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide= models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden=models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Insurance Certificate Documents"


class HullIdentificationNumberDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/hull_identification_number_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    proposal = models.ForeignKey(Proposal,related_name='hull_identification_number_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide= models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden=models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Hull Identification Number Documents"


class ElectoralRollDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/electoral_roll_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    #emailuser = models.ForeignKey(EmailUser,related_name='electoral_roll_documents')
    proposal = models.ForeignKey(Proposal,related_name='electoral_roll_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255,null=True,blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide= models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden=models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Electoral Roll Document"


class MooringReportDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/mooring_report_documents/{filename}'

    def upload_to(self, filename):
        # proposal_id = self.proposal.id
        proposal = self.proposal_set.first()
        return self.relative_path_to_file(proposal.id, filename)

    # proposal = models.ForeignKey(Proposal, related_name='mooring_report_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide = models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden = models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Mooring Report Document"


class WrittenProofDocument(Document):
    # proposal = models.ForeignKey(Proposal, related_name='written_proof_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/written_proof_documents/{filename}'

    def upload_to(self, filename):
        # proposal_id = self.proposal.id
        proposal = self.proposal_set.first()
        return self.relative_path_to_file(proposal.id, filename)

    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted
    can_hide = models.BooleanField(default=False) # after initial submit, document cannot be deleted but can be hidden
    hidden = models.BooleanField(default=False) # after initial submit prevent document from being deleted

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Written Proof Document"


class SignedLicenceAgreementDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/signed_licence_agreement_documents/{filename}'

    def upload_to(self, filename):
        # proposal_id = self.proposal.id
        proposal = self.proposal_set.first()
        return self.relative_path_to_file(proposal.id, filename)

    # proposal = models.ForeignKey(Proposal, related_name='signed_licence_agreement_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(default=True)
    can_hide = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Signed Licence Agreement"


class ProofOfIdentityDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/proof_of_identity_documents/{filename}'

    def upload_to(self, filename):
        # proposal_id = self.proposal.id
        proposal = self.proposal_set.first()
        return self.relative_path_to_file(proposal.id, filename)
    # proposal = models.ForeignKey(Proposal, related_name='proof_of_identity_documents', on_delete=models.CASCADE)
    # _file = models.FileField(max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(default=True)
    can_hide = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Proof Of Identity"


class ProposalRequest(models.Model):
    proposal = models.ForeignKey(Proposal, related_name='proposalrequest_set', on_delete=models.CASCADE)
    subject = models.CharField(max_length=200, blank=True)
    text = models.TextField(blank=True)
    # officer = models.ForeignKey(EmailUser, null=True, on_delete=models.SET_NULL)
    officer = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return '{} - {}'.format(self.subject, self.text)

    class Meta:
        app_label = 'mooringlicensing'


class ComplianceRequest(ProposalRequest):
    REASON_CHOICES = (('outstanding', 'There are currently outstanding returns for the previous licence'),
                      ('other', 'Other'))
    reason = models.CharField('Reason', max_length=30, choices=REASON_CHOICES, default=REASON_CHOICES[0][0])

    class Meta:
        app_label = 'mooringlicensing'


class AmendmentReason(models.Model):
    reason = models.CharField('Reason', max_length=125)

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Application Amendment Reason" # display name in Admin
        verbose_name_plural = "Application Amendment Reasons"

    def __str__(self):
        return self.reason


# class BackToAssessor(models.Model):
#     details = models.TextField(blank=True)
#     proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE)
#
#     class Meta:
#         app_label = 'mooringlicensing'
#
#     def __str__(self):
#         return self.details


class AmendmentRequest(ProposalRequest):
    STATUS_CHOICES = (('requested', 'Requested'), ('amended', 'Amended'))

    status = models.CharField('Status', max_length=30, choices=STATUS_CHOICES, default=STATUS_CHOICES[0][0])
    reason = models.ForeignKey(AmendmentReason, blank=True, null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = 'mooringlicensing'

    def generate_amendment(self,request):
        with transaction.atomic():
            try:
                if not self.proposal.can_assess(request.user):
                    raise exceptions.ProposalNotAuthorized()
                if self.status == 'requested':
                    proposal = self.proposal
                    if proposal.processing_status != Proposal.PROCESSING_STATUS_DRAFT:
                        proposal.processing_status = Proposal.PROCESSING_STATUS_DRAFT
                        proposal.save()
                        logger.info(f'Status: [{Proposal.PROCESSING_STATUS_DRAFT}] has been set to the proposal: [{proposal}]')
                    # Create a log entry for the proposal
                    proposal.log_user_action(ProposalUserAction.ACTION_ID_REQUEST_AMENDMENTS, request)
                    # Create a log entry for the organisation
                    applicant_field = getattr(proposal, proposal.applicant_field)
                    # applicant_field.log_user_action(ProposalUserAction.ACTION_ID_REQUEST_AMENDMENTS, request)

                    # send email

                    send_amendment_email_notification(self, request, proposal)

                self.save()
            except:
                raise


class ProposalDeclinedDetails(models.Model):
    proposal = models.OneToOneField(Proposal, null=True, on_delete=models.SET_NULL)
    # officer = models.ForeignKey(EmailUser, null=True, on_delete=models.SET_NULL)
    officer = models.IntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    cc_email = models.TextField(null=True)

    class Meta:
        app_label = 'mooringlicensing'


# @python_2_unicode_compatible
class ProposalStandardRequirement(RevisionedMixin):
    text = models.TextField()
    code = models.CharField(max_length=10, unique=True)
    obsolete = models.BooleanField(default=False)
    application_type = models.ForeignKey(ApplicationType, null=True, blank=True, on_delete=models.CASCADE)
    participant_number_required = models.BooleanField(default=False)
    default = models.BooleanField(default=False)

    def __str__(self):
        return self.code

    class Meta:
        app_label = 'mooringlicensing'
        verbose_name = "Application Standard Requirement"
        verbose_name_plural = "Application Standard Requirements"


class ProposalUserAction(UserAction):
    ACTION_CREATE_CUSTOMER_ = "Create customer {}"
    ACTION_CREATE_PROFILE_ = "Create profile {}"
    ACTION_LODGE_APPLICATION = "Lodge application {}"
    ACTION_ASSIGN_TO_ASSESSOR = "Assign application {} to {} as the assessor"
    ACTION_UNASSIGN_ASSESSOR = "Unassign assessor from application {}"
    ACTION_ASSIGN_TO_APPROVER = "Assign application {} to {} as the approver"
    ACTION_UNASSIGN_APPROVER = "Unassign approver from application {}"
    ACTION_ACCEPT_ID = "Accept ID"
    ACTION_RESET_ID = "Reset ID"
    ACTION_ID_REQUEST_UPDATE = 'Request ID update'
    ACTION_ACCEPT_CHARACTER = 'Accept character'
    ACTION_RESET_CHARACTER = "Reset character"
    ACTION_ACCEPT_REVIEW = 'Accept review'
    ACTION_RESET_REVIEW = "Reset review"
    ACTION_ID_REQUEST_AMENDMENTS = "Request amendments"
    ACTION_SEND_FOR_ASSESSMENT_TO_ = "Send for assessment to {}"
    ACTION_SEND_ASSESSMENT_REMINDER_TO_ = "Send assessment reminder to {}"
    ACTION_DECLINE = "Decline application {}"
    ACTION_ENTER_CONDITIONS = "Enter requirement"
    ACTION_CREATE_CONDITION_ = "Create requirement {}"
    ACTION_ISSUE_APPROVAL_ = "Issue Approval for application {}"
    ACTION_AWAITING_PAYMENT_APPROVAL_ = "Awaiting Payment for application {}"
    ACTION_PRINTING_STICKER = "Printing Sticker for application {}"
    ACTION_STICKER_TO_BE_RETURNED = "Sticker to be returned for application {}"
    ACTION_APPROVE_APPLICATION = "Approve application {}"
    ACTION_UPDATE_APPROVAL_ = "Update Approval for application {}"
    ACTION_APPROVED = "Grant application {}"
    ACTION_AUTO_APPROVED = "Grant application {}"
    ACTION_EXPIRED_APPROVAL_ = "Expire Approval for proposal {}"
    ACTION_DISCARD_PROPOSAL = "Discard application {}"
    ACTION_WITHDRAW_PROPOSAL = "Withdraw application {}"
    ACTION_APPROVAL_LEVEL_DOCUMENT = "Assign Approval level document {}"
    ACTION_SUBMIT_OTHER_DOCUMENTS = 'Submit other documents'
    # Assessors
    ACTION_SAVE_ASSESSMENT_ = "Save assessment {}"
    ACTION_CONCLUDE_ASSESSMENT_ = "Conclude assessment {}"
    ACTION_PROPOSED_APPROVAL = "Application {} has been proposed for approval"
    ACTION_PROPOSED_DECLINE = "Application {} has been proposed for decline"

    ACTION_ENTER_REQUIREMENTS = "Enter Requirements for proposal {}"
    ACTION_BACK_TO_PROCESSING = "Back to processing for proposal {}"

    #Approval
    ACTION_REISSUE_APPROVAL = "Reissue approval for application {}"
    ACTION_CANCEL_APPROVAL = "Cancel approval for application {}"
    ACTION_EXTEND_APPROVAL = "Extend approval"
    ACTION_SUSPEND_APPROVAL = "Suspend approval for application {}"
    ACTION_REINSTATE_APPROVAL = "Reinstate approval for application {}"
    ACTION_SURRENDER_APPROVAL = "Surrender approval for application {}"
    ACTION_RENEW_PROPOSAL = "Create Renewal application for application {}"
    ACTION_AMEND_PROPOSAL = "Create Amendment application for application {}"
    ACTION_SWAP_MOORINGS_PROPOSAL = "Create Swap moorings application for application {}"
    #Vessel
    ACTION_CREATE_VESSEL = "Create Vessel {}"
    ACTION_EDIT_VESSEL= "Edit Vessel {}"
    ACTION_PUT_ONHOLD = "Put Application On-hold {}"
    ACTION_REMOVE_ONHOLD = "Remove Application On-hold {}"
    ACTION_WITH_QA_OFFICER = "Send Application QA Officer {}"
    ACTION_QA_OFFICER_COMPLETED = "QA Officer Assessment Completed {}"

    # monthly invoicing by cron
    ACTION_SEND_BPAY_INVOICE = "Send BPAY invoice {} for application {} to {}"
    ACTION_SEND_MONTHLY_INVOICE = "Send monthly invoice {} for application {} to {}"
    ACTION_SEND_MONTHLY_CONFIRMATION = "Send monthly confirmation for booking ID {}, for application {} to {}"
    ACTION_SEND_PAYMENT_DUE_NOTIFICATION = "Send monthly invoice/BPAY payment due notification {} for application {} to {}"

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ('-when',)

    @classmethod
    def log_action(cls, proposal, action, user=None):
        return cls.objects.create(
            proposal=proposal,
            who=user.id if isinstance(user, EmailUserRO) else user,
            what=str(action)
        )

    # who = models.ForeignKey(EmailUser, null=True, blank=True, on_delete=models.SET_NULL)
    who = models.IntegerField(null=True, blank=True)
    when = models.DateTimeField(null=False, blank=False, auto_now_add=True)
    what = models.TextField(blank=False)
    proposal = models.ForeignKey(Proposal, related_name='action_logs', on_delete=models.CASCADE)


class ProposalRequirement(OrderedModel):
    RECURRENCE_PATTERNS = [(1, 'Weekly'), (2, 'Monthly'), (3, 'Yearly')]
    standard_requirement = models.ForeignKey(ProposalStandardRequirement,null=True,blank=True,on_delete=models.SET_NULL)
    free_requirement = models.TextField(null=True,blank=True)
    standard = models.BooleanField(default=True)
    proposal = models.ForeignKey(Proposal,related_name='requirements',on_delete=models.CASCADE)
    due_date = models.DateField(null=True,blank=True)
    recurrence = models.BooleanField(default=False)
    recurrence_pattern = models.SmallIntegerField(choices=RECURRENCE_PATTERNS,default=1)
    recurrence_schedule = models.IntegerField(null=True,blank=True)
    copied_from = models.ForeignKey('self', on_delete=models.SET_NULL, blank=True, null=True)
    is_deleted = models.BooleanField(default=False)
    copied_for_renewal = models.BooleanField(default=False)
    require_due_date = models.BooleanField(default=False)

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def requirement(self):
        return self.standard_requirement.text if self.standard else self.free_requirement

    def can_referral_edit(self,user):
        if self.proposal.processing_status=='with_referral':
            if self.referral_group:
                group =  ReferralRecipientGroup.objects.filter(id=self.referral_group.id)
                #user=request.user
                if group and group[0] in user.referralrecipientgroup_set.all():
                    return True
                else:
                    return False
        return False

    def can_district_assessor_edit(self,user):
        allowed_status=['with_district_assessor', 'partially_approved', 'partially_declined']
        if self.district_proposal and self.district_proposal.processing_status=='with_assessor_requirements' and self.proposal.processing_status in allowed_status:
            if self.district_proposal.can_process_requirements(user):
                return True
        return False

    def add_documents(self, request):
        with transaction.atomic():
            try:
                # save the files
                data = json.loads(request.data.get('data'))
                if not data.get('update'):
                    documents_qs = self.requirement_documents.filter(input_name='requirement_doc', visible=True)
                    documents_qs.delete()
                for idx in range(data['num_files']):
                    _file = request.data.get('file-'+str(idx))
                    document = self.requirement_documents.create(
                        _file=_file, 
                        name=_file.name,
                        input_name = data['input_name'],
                        can_delete = True
                    )
                # end save documents
                self.save()
            except:
                raise
        return


@receiver(pre_delete, sender=Proposal)
def delete_documents(sender, instance, *args, **kwargs):
    for document in instance.documents.all():
        document.delete()

def clone_proposal_with_status_reset(original_proposal):
    with transaction.atomic():
        try:
            proposal = type(original_proposal.child_obj).objects.create()
            proposal.processing_status = Proposal.PROCESSING_STATUS_DRAFT
            proposal.previous_application = original_proposal
            proposal.approval = original_proposal.approval
            proposal.null_vessel_on_create = not original_proposal.vessel_on_proposal()

            proposal.save(no_revision=True)
            return proposal
        except:
            raise

def searchKeyWords(searchWords, searchProposal, searchApproval, searchCompliance, is_internal= True):
    from mooringlicensing.utils import search, search_approval, search_compliance
    from mooringlicensing.components.approvals.models import Approval
    from mooringlicensing.components.compliances.models import Compliance
    qs = []
    application_types=[ApplicationType.TCLASS, ApplicationType.EVENT, ApplicationType.FILMING]
    if is_internal:
        # proposal_list = Proposal.objects.filter(application_type__name__in=application_types).exclude(processing_status__in=['discarded','draft'])
        proposal_list = Proposal.objects.filter(application_type__name__in=application_types).exclude(processing_status__in=[Proposal.PROCESSING_STATUS_DISCARDED, Proposal.PROCESSING_STATUS_DRAFT,])
        approval_list = Approval.objects.all().order_by('lodgement_number', '-issue_date').distinct('lodgement_number')
        compliance_list = Compliance.objects.all()
    if searchWords:
        if searchProposal:
            for p in proposal_list:
                if p.search_data:
                    try:
                        results = search(p.search_data, searchWords)
                        final_results = {}
                        if results:
                            for r in results:
                                for key, value in r.items():
                                    final_results.update({'key': key, 'value': value})
                            res = {
                                'number': p.lodgement_number,
                                'id': p.id,
                                'type': 'Proposal',
                                'applicant': p.applicant,
                                'text': final_results,
                                }
                            qs.append(res)
                    except:
                        raise
        if searchApproval:
            for a in approval_list:
                try:
                    results = search_approval(a, searchWords)
                    qs.extend(results)
                except:
                    raise
        if searchCompliance:
            for c in compliance_list:
                try:
                    results = search_compliance(c, searchWords)
                    qs.extend(results)
                except:
                    raise
    return qs

def search_reference(reference_number):
    from mooringlicensing.components.approvals.models import Approval
    from mooringlicensing.components.compliances.models import Compliance
    proposal_list = Proposal.objects.all().exclude(processing_status__in=['discarded'])
    approval_list = Approval.objects.all().order_by('lodgement_number', '-issue_date').distinct('lodgement_number')
    compliance_list = Compliance.objects.all().exclude(processing_status__in=['future'])
    record = {}
    try:
        result = proposal_list.get(lodgement_number = reference_number)
        record = {  'id': result.id,
                    'type': 'proposal' }
    except Proposal.DoesNotExist:
        try:
            result = approval_list.get(lodgement_number = reference_number)
            record = {  'id': result.id,
                        'type': 'approval' }
        except Approval.DoesNotExist:
            try:
                for c in compliance_list:
                    if c.reference == reference_number:
                        record = {  'id': c.id,
                                    'type': 'compliance' }
            except:
                raise ValidationError('Record with provided reference number does not exist')
    if record:
        return record
    else:
        raise ValidationError('Record with provided reference number does not exist')

from ckeditor.fields import RichTextField
class HelpPage(models.Model):
    HELP_TEXT_EXTERNAL = 1
    HELP_TEXT_INTERNAL = 2
    HELP_TYPE_CHOICES = (
        (HELP_TEXT_EXTERNAL, 'External'),
        (HELP_TEXT_INTERNAL, 'Internal'),
    )

    content = RichTextField()
    description = models.CharField(max_length=256, blank=True, null=True)
    help_type = models.SmallIntegerField('Help Type', choices=HELP_TYPE_CHOICES, default=HELP_TEXT_EXTERNAL)
    version = models.SmallIntegerField(default=1, blank=False, null=False)

    class Meta:
        app_label = 'mooringlicensing'
        unique_together = (
                'help_type',
                'version'
                )


import reversion

reversion.register(ProposalDocument, follow=['approval_level_document'])
reversion.register(RequirementDocument, follow=[])
reversion.register(ProposalType, follow=['proposal_set',])
# TODO: fix this to improve performance
#reversion.register(Proposal, follow=['documents', 'succeeding_proposals', 'comms_logs', 'companyownership_set', 'insurance_certificate_documents', 'hull_identification_number_documents', 'electoral_roll_documents', 'mooring_report_documents', 'written_proof_documents', 'signed_licence_agreement_documents', 'proof_of_identity_documents', 'proposalrequest_set', 'proposaldeclineddetails', 'action_logs', 'requirements', 'application_fees', 'approval_history_records', 'approvals', 'sticker_set', 'compliances'])
reversion.register(Proposal, follow=['proposalapplicant_set'])
reversion.register(ProposalApplicant)
reversion.register(StickerPrintingContact, follow=[])
reversion.register(StickerPrintingBatch, follow=['sticker_set'])
reversion.register(StickerPrintingResponseEmail, follow=['stickerprintingresponse_set'])
reversion.register(StickerPrintingResponse, follow=['sticker_set'])
reversion.register(WaitingListApplication, follow=['documents', 'succeeding_proposals', 'comms_logs', 'companyownership_set', 'insurance_certificate_documents', 'hull_identification_number_documents', 'electoral_roll_documents', 'mooring_report_documents', 'written_proof_documents', 'signed_licence_agreement_documents', 'proof_of_identity_documents', 'proposalrequest_set', 'proposaldeclineddetails', 'action_logs', 'requirements', 'approval_history_records', 'approvals', 'sticker_set', 'compliances'])
reversion.register(AnnualAdmissionApplication, follow=['documents', 'succeeding_proposals', 'comms_logs', 'companyownership_set', 'insurance_certificate_documents', 'hull_identification_number_documents', 'electoral_roll_documents', 'mooring_report_documents', 'written_proof_documents', 'signed_licence_agreement_documents', 'proof_of_identity_documents', 'proposalrequest_set', 'proposaldeclineddetails', 'action_logs', 'requirements', 'approval_history_records', 'approvals', 'sticker_set', 'compliances'])
reversion.register(AuthorisedUserApplication, follow=['documents', 'succeeding_proposals', 'comms_logs', 'companyownership_set', 'insurance_certificate_documents', 'hull_identification_number_documents', 'electoral_roll_documents', 'mooring_report_documents', 'written_proof_documents', 'signed_licence_agreement_documents', 'proof_of_identity_documents', 'proposalrequest_set', 'proposaldeclineddetails', 'action_logs', 'requirements', 'approval_history_records', 'approvals', 'sticker_set', 'compliances'])
reversion.register(MooringLicenceApplication, follow=['documents', 'succeeding_proposals', 'comms_logs', 'companyownership_set', 'insurance_certificate_documents', 'hull_identification_number_documents', 'electoral_roll_documents', 'mooring_report_documents', 'written_proof_documents', 'signed_licence_agreement_documents', 'proof_of_identity_documents', 'proposalrequest_set', 'proposaldeclineddetails', 'action_logs', 'requirements', 'approval_history_records', 'approvals', 'sticker_set', 'compliances'])
reversion.register(ProposalLogDocument, follow=[])
reversion.register(ProposalLogEntry, follow=['documents'])
reversion.register(MooringBay, follow=['proposal_set', 'mooring_set'])
reversion.register(Mooring, follow=['proposal_set', 'ria_generated_proposal', 'comms_logs', 'action_logs', 'mooringonapproval_set', 'approval_set'])
reversion.register(MooringLogDocument, follow=[])
reversion.register(MooringLogEntry, follow=['documents'])
reversion.register(MooringUserAction, follow=[])
reversion.register(Vessel, follow=['comms_logs', 'vesseldetails_set', 'companyownership_set', 'vesselownership_set', 'owner_set', 'company_set'])
reversion.register(VesselLogDocument, follow=[])
reversion.register(VesselLogEntry, follow=['documents'])
reversion.register(VesselDetails, follow=['proposal_set'])
reversion.register(CompanyOwnership, follow=['blocking_proposal', 'vessel', 'company'])
reversion.register(VesselOwnership, follow=['owner', 'vessel', 'company_ownerships'])
reversion.register(VesselRegistrationDocument, follow=[])
reversion.register(Owner, follow=['vesselownership_set'])
reversion.register(Company, follow=['companyownership_set'])
reversion.register(InsuranceCertificateDocument, follow=[])
reversion.register(HullIdentificationNumberDocument, follow=[])
reversion.register(ElectoralRollDocument, follow=[])
reversion.register(MooringReportDocument, follow=[])
reversion.register(WrittenProofDocument, follow=[])
reversion.register(SignedLicenceAgreementDocument, follow=[])
reversion.register(ProofOfIdentityDocument, follow=[])
reversion.register(ProposalRequest, follow=[])
reversion.register(ComplianceRequest, follow=[])
reversion.register(AmendmentReason, follow=['amendmentrequest_set'])
reversion.register(AmendmentRequest, follow=[])
reversion.register(ProposalDeclinedDetails, follow=[])
reversion.register(ProposalStandardRequirement, follow=['proposalrequirement_set'])
reversion.register(ProposalUserAction, follow=[])
reversion.register(ProposalRequirement, follow=['requirement_documents', 'proposalrequirement_set', 'compliance_requirement'])
reversion.register(HelpPage, follow=[])
