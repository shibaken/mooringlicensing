import math
from dateutil.relativedelta import relativedelta

import ledger_api_client.utils
from django.core.files.base import ContentFile

import datetime
import logging
import re
import uuid

import pytz
from django.db import models,transaction
from django.dispatch import receiver
from django.db.models.signals import pre_delete
from django.db.models import Count
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.contrib.postgres.fields.jsonb import JSONField
from django.utils import timezone
from django.conf import settings
from django.db.models import Q

from mooringlicensing.ledger_api_utils import retrieve_email_userro, get_invoice_payment_status
# from ledger.settings_base import TIME_ZONE
from mooringlicensing.settings import PROPOSAL_TYPE_SWAP_MOORINGS, TIME_ZONE, GROUP_DCV_PERMIT_ADMIN, PRIVATE_MEDIA_STORAGE_LOCATION, PRIVATE_MEDIA_BASE_URL
# from ledger.accounts.models import EmailUser, RevisionedMixin
# from ledger.payments.invoice.models import Invoice
from ledger_api_client.ledger_models import EmailUserRO as EmailUser, Invoice, EmailUserRO
from mooringlicensing.components.approvals.pdf import create_dcv_permit_document, create_dcv_admission_document, \
    create_approval_doc, create_renewal_doc
from mooringlicensing.components.emails.utils import get_public_url
from mooringlicensing.components.organisations.models import Organisation
from mooringlicensing.components.payments_ml.models import StickerActionFee, FeeConstructor
from mooringlicensing.components.proposals.models import Proposal, ProposalUserAction, MooringBay, Mooring, \
    StickerPrintingBatch, StickerPrintingResponse, Vessel, VesselOwnership, ProposalType
from mooringlicensing.components.main.models import CommunicationsLogEntry, UserAction, Document, \
    GlobalSettings, RevisionedMixin, ApplicationType  # , ApplicationType
from mooringlicensing.components.approvals.email import (
    send_approval_expire_email_notification,
    send_approval_cancel_email_notification,
    send_approval_suspend_email_notification,
    send_approval_reinstate_email_notification,
    send_approval_surrender_email_notification,
    send_aup_revoked_due_to_mooring_swap_email,
    # send_auth_user_no_moorings_notification,
    send_auth_user_mooring_removed_notification,
    send_swap_moorings_application_created_notification,
)
from mooringlicensing.helpers import is_customer
from mooringlicensing.settings import PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_NEW
from ledger_api_client.utils import calculate_excl_gst
from django.core.files.storage import FileSystemStorage

private_storage = FileSystemStorage(  # We want to store files in secure place (outside of the media folder)
    location=PRIVATE_MEDIA_STORAGE_LOCATION,
    base_url=PRIVATE_MEDIA_BASE_URL,
)

# logger = logging.getLogger('mooringlicensing')
logger = logging.getLogger(__name__)


def update_waiting_list_offer_doc_filename(instance, filename):
    return '{}/proposals/{}/approvals/{}/waiting_list_offer/{}'.format(settings.MEDIA_APP_DIR, instance.approval.current_proposal.id, instance.id, filename)

def update_approval_doc_filename(instance, filename):
    # return '{}/proposals/{}/approvals/{}'.format(settings.MEDIA_APP_DIR, instance.approval.current_proposal.id,filename)
    return 'proposal/{}/approvals/{}'.format(instance.approval.current_proposal.id,filename)

def update_approval_comms_log_filename(instance, filename):
    return '{}/proposals/{}/approvals/communications/{}'.format(settings.MEDIA_APP_DIR, instance.log_entry.approval.current_proposal.id,filename)


class WaitingListOfferDocument(Document):
    @staticmethod
    def relative_path_to_file(approval_id, filename):
        return f'approval/{approval_id}/waiting_list_offer_documents/{filename}'

    def upload_to(self, filename):
        approval_id = self.approval.id
        return self.relative_path_to_file(approval_id, filename)

    approval = models.ForeignKey('Approval',related_name='waiting_list_offer_documents', on_delete=models.CASCADE)
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
        verbose_name = "Waiting List Offer Documents"


class RenewalDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/renewal_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.approval.current_proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    approval = models.ForeignKey('Approval',related_name='renewal_documents', on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_approval_doc_filename, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted

    def delete(self):
        if self.can_delete:
            return super(RenewalDocument, self).delete()
        logger.info('Cannot delete existing document object after Proposal has been submitted (including document submitted before Proposal pushback to status Draft): {}'.format(self.name))

    class Meta:
        app_label = 'mooringlicensing'


class AuthorisedUserSummaryDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/authorised_user_summary_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.approval.current_proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    approval = models.ForeignKey('Approval', related_name='authorised_user_summary_documents', on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_approval_doc_filename, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )

    class Meta:
        app_label = 'mooringlicensing'


class ApprovalDocument(Document):
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/approval_documents/{filename}'

    def upload_to(self, filename):
        proposal_id = self.approval.current_proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    # def update_approval_doc_filename(instance, filename):
    #     return 'proposal/{}/approvals/{}'.format(instance.approval.current_proposal.id,filename)

    approval = models.ForeignKey('Approval', related_name='approval_documents', on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_approval_doc_filename, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    can_delete = models.BooleanField(default=True) # after initial submit prevent document from being deleted

    def delete(self):
        if self.can_delete:
            return super(ApprovalDocument, self).delete()
        logger.info('Cannot delete existing document object after Application has been submitted (including document submitted before Application pushback to status Draft): {}'.format(self.name))

    class Meta:
        app_label = 'mooringlicensing'


class MooringOnApproval(RevisionedMixin):
    """
    This class is used only for AUP, because an AUP can have multiple moorings.
    """
    approval = models.ForeignKey('Approval', on_delete=models.CASCADE)
    mooring = models.ForeignKey(Mooring, on_delete=models.CASCADE)
    sticker = models.ForeignKey('Sticker', blank=True, null=True, on_delete=models.SET_NULL)
    site_licensee = models.BooleanField()
    end_date = models.DateField(blank=True, null=True)

    def __str__(self):
        approval = self.approval.lodgement_number if self.approval else ' '
        mooring = self.mooring.name if self.mooring else ' '
        sticker = self.sticker.number if self.sticker else ' '
        return 'ID:{} ({}-{}-{})'.format(self.id, approval, mooring, sticker)

    def save(self, *args, **kwargs):
        existing_ria_moorings = MooringOnApproval.objects.filter(approval=self.approval, mooring=self.mooring, site_licensee=False).count()
        if existing_ria_moorings >= 2 and not self.site_licensee:
            raise ValidationError('Maximum of two RIA selected moorings allowed per Authorised User Permit')

        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(MooringOnApproval, self).save(*args,**kwargs)

    class Meta:
        app_label = 'mooringlicensing'
        unique_together = ("mooring", "approval")

    @staticmethod
    def get_current_moas_by_approval(approval):
        no_end_date = Q(end_date__isnull=True)
        ml_is_current = Q(mooring__mooring_licence__status__in=MooringLicence.STATUSES_AS_CURRENT)
        sticker_is_current = Q(sticker__status__in=Sticker.STATUSES_AS_CURRENT)
        moas = approval.mooringonapproval_set.filter((no_end_date & ml_is_current) & sticker_is_current)  # Is (end_date_is_not_set | ml_is_current) correct?
        return moas

    @staticmethod
    def get_moas_to_be_removed_by_approval(approval):
        has_end_date = Q(end_date__isnull=False)
        ml_is_not_current = ~Q(mooring__mooring_licence__status__in=MooringLicence.STATUSES_AS_CURRENT)
        sticker_is_current = Q(sticker__status__in=Sticker.STATUSES_AS_CURRENT)
        moas = approval.mooringonapproval_set.filter((has_end_date | ml_is_not_current) & sticker_is_current)
        return moas

class VesselOwnershipOnApproval(RevisionedMixin):
    """
    This model is used only for the MooringLicence because ML can have multiple vessels
    """
    approval = models.ForeignKey('Approval', on_delete=models.CASCADE)
    vessel_ownership = models.ForeignKey(VesselOwnership, on_delete=models.CASCADE)
    end_date = models.DateField(blank=True, null=True)

    def __str__(self):
        return 'ID:{} ({}-{})'.format(self.id, self.approval, self.vessel_ownership)

    class Meta:
        app_label = 'mooringlicensing'
        unique_together = ("vessel_ownership", "approval")


class ApprovalHistory(RevisionedMixin):
    #REASON_NEW = 'new'
    #REASON_REPLACEMENT_STICKER = 'replacement_sticker'
    #REASON_VESSEL_ADD = 'vessel_add'
    #REASON_VESSEL_SOLD = 'vessel_sold'
    #REASON_MOORING_ADD = 'mooring_add'
    #REASON_MOORING_SWAP = 'mooring_swap'

    #REASON_CHOICES = (
    #    (REASON_NEW, 'New'),
    #    (REASON_REPLACEMENT_STICKER, 'Replacement sticker'),
    #    (REASON_VESSEL_ADD, 'Vessel added'),
    #    (REASON_VESSEL_SOLD, 'Vessel sold'),
    #    (REASON_MOORING_ADD, 'New mooring'),
    #    (REASON_MOORING_SWAP, 'Mooring swap'),
    #)

    #reason = models.CharField(max_length=40, choices=REASON_CHOICES, blank=True, null=True)
    reason = models.CharField(max_length=100, blank=True, null=True)
                                       #default=REASON_CHOICES[0][0])
    approval = models.ForeignKey('Approval', on_delete=models.CASCADE)
    # can be null due to requirement to allow null vessels on renewal/amendment applications
    vessel_ownership = models.ForeignKey(VesselOwnership, blank=True, null=True, on_delete=models.SET_NULL)
    proposal = models.ForeignKey(Proposal, related_name='approval_history_records', on_delete=models.CASCADE)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(blank=True, null=True)
    stickers = models.ManyToManyField('Sticker')
    approval_letter = models.ForeignKey(ApprovalDocument, blank=True, null=True, on_delete=models.SET_NULL)
    # derive from proposal

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ['-id',]

    def __str__(self):
        return '{}, {}'.format(self.id, self.reason)


class Approval(RevisionedMixin):
    APPROVAL_STATUS_CURRENT = 'current'
    APPROVAL_STATUS_EXPIRED = 'expired'
    APPROVAL_STATUS_CANCELLED = 'cancelled'
    APPROVAL_STATUS_SURRENDERED = 'surrendered'
    APPROVAL_STATUS_SUSPENDED = 'suspended'
    APPROVAL_STATUS_FULFILLED = 'fulfilled'

    STATUS_CHOICES = (
        (APPROVAL_STATUS_CURRENT, 'Current'),
        (APPROVAL_STATUS_EXPIRED, 'Expired'),
        (APPROVAL_STATUS_CANCELLED, 'Cancelled'),
        (APPROVAL_STATUS_SURRENDERED, 'Surrendered'),
        (APPROVAL_STATUS_SUSPENDED, 'Suspended'),
        (APPROVAL_STATUS_FULFILLED, 'Fulfilled'),
    )
    # waiting list allocation approvals
    INTERNAL_STATUS_WAITING = 'waiting' #b #y
    INTERNAL_STATUS_OFFERED = 'offered' #b - no change to queue #y
    INTERNAL_STATUS_SUBMITTED = 'submitted' #c - no change to queue #y
    INTERNAL_STATUS_APPROVED = 'approved'
    INTERNAL_STATUS_CHOICES = (
        (INTERNAL_STATUS_WAITING, 'Waiting for offer'),
        (INTERNAL_STATUS_OFFERED, 'Mooring Site Licence offered'),
        (INTERNAL_STATUS_SUBMITTED, 'Mooring Site Licence application submitted'),
        (INTERNAL_STATUS_APPROVED, 'Mooring Site Licence application approved'),
    )
    APPROVED_STATUSES = [
        APPROVAL_STATUS_CURRENT,
        APPROVAL_STATUS_SURRENDERED,
        APPROVAL_STATUS_FULFILLED,
    ]
    lodgement_number = models.CharField(max_length=9, blank=True, unique=True)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES,
                                       default=STATUS_CHOICES[0][0])
    internal_status = models.CharField(max_length=40, choices=INTERNAL_STATUS_CHOICES, blank=True, null=True)
    licence_document = models.ForeignKey(ApprovalDocument, blank=True, null=True, related_name='licence_document', on_delete=models.SET_NULL)
    authorised_user_summary_document = models.ForeignKey(AuthorisedUserSummaryDocument, blank=True, null=True, related_name='approvals', on_delete=models.SET_NULL)
    cover_letter_document = models.ForeignKey(ApprovalDocument, blank=True, null=True, related_name='cover_letter_document', on_delete=models.SET_NULL)
    replaced_by = models.OneToOneField('self', blank=True, null=True, related_name='replace', on_delete=models.SET_NULL)
    current_proposal = models.ForeignKey(Proposal,related_name='approvals', null=True, on_delete=models.SET_NULL)
    renewal_document = models.ForeignKey(RenewalDocument, blank=True, null=True, related_name='renewal_document', on_delete=models.SET_NULL)
    renewal_sent = models.BooleanField(default=False)
    issue_date = models.DateTimeField()
    wla_queue_date = models.DateTimeField(blank=True, null=True)
    original_issue_date = models.DateField(auto_now_add=True)
    start_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    surrender_details = JSONField(blank=True,null=True)
    suspension_details = JSONField(blank=True,null=True)
    # submitter = models.ForeignKey(EmailUser, on_delete=models.CASCADE, blank=True, null=True, related_name='mooringlicensing_approvals')
    submitter = models.IntegerField(blank=True, null=True)
    org_applicant = models.ForeignKey(Organisation, on_delete=models.CASCADE, blank=True, null=True, related_name='org_approvals')
    # proxy_applicant = models.ForeignKey(EmailUser, on_delete=models.CASCADE, blank=True, null=True, related_name='proxy_approvals')
    proxy_applicant = models.IntegerField(blank=True, null=True)
    extracted_fields = JSONField(blank=True, null=True)
    cancellation_details = models.TextField(blank=True)
    extend_details = models.TextField(blank=True)
    cancellation_date = models.DateField(blank=True, null=True)
    set_to_cancel = models.BooleanField(default=False)
    set_to_suspend = models.BooleanField(default=False)
    set_to_surrender = models.BooleanField(default=False)

    renewal_count = models.PositiveSmallIntegerField('Number of times an Approval has been renewed', default=0)
    migrated=models.BooleanField(default=False)
    expiry_notice_sent = models.BooleanField(default=False)
    # for cron job
    exported = models.BooleanField(default=False) # must be False after every add/edit
    moorings = models.ManyToManyField(Mooring, through=MooringOnApproval)
    vessel_ownerships = models.ManyToManyField(VesselOwnership, through=VesselOwnershipOnApproval)
    wla_order = models.PositiveIntegerField(help_text='wla order per mooring bay', null=True)
    vessel_nomination_reminder_sent = models.BooleanField(default=False)
    reissued= models.BooleanField(default=False)
    # mark as True when Approval is re/issued
    export_to_mooring_booking = models.BooleanField(default=False)

    class Meta:
        app_label = 'mooringlicensing'
        unique_together = ('lodgement_number', 'issue_date')
        ordering = ['-id',]

    @property
    def grace_period_end_date(self):
        return self.child_obj.get_grace_period_end_date()

    def cancel_existing_annual_admission_permit(self, current_date):
        # Cancel existing annual admission permit for the same vessl if exists
        if self.current_proposal and self.current_proposal.vessel_ownership:
            target_vessel = self.current_proposal.vessel_ownership.vessel
            aaps = target_vessel.get_current_aaps(current_date)
            if aaps.count() == 1:
                aap = aaps[0]
                aap.status = Approval.APPROVAL_STATUS_CANCELLED
                aap.save()
                logger.info(f'Approval: {aap.lodgement_number} for the vessel: [{target_vessel}] has been cancelled because the AUP: [{self}] for the same vessel has been created.')

                # Set 'to_be_returned' status to the sticker(s) for the aap
                stickers = aap.stickers.filter(vessel_ownership__vessel=target_vessel)
                self._update_status_of_sticker_to_be_removed(stickers)

            elif aaps.count() > 1:
                logger.warning(f'Vessel: [{target_vessel}] has more than one current annual admission permits.')

    @property
    def submitter_obj(self):
        return retrieve_email_userro(self.submitter) if self.submitter else None

    def get_max_fee_item(self, fee_season, vessel_details=None):
        max_fee_item = None
        for proposal in self.proposal_set.all():
            fee_items = proposal.get_fee_items_paid(fee_season, vessel_details)

            for fee_item in fee_items:
                if not max_fee_item:
                    max_fee_item = fee_item
                else:
                    if max_fee_item.get_absolute_amount() < fee_item.get_absolute_amount():
                        max_fee_item = fee_item
        return max_fee_item

    def get_licence_document_as_attachment(self):
        attachment = None
        if self.licence_document:
            licence_document = self.licence_document._file
            if licence_document is not None:
                file_name = self.licence_document.name
                attachment = (file_name, licence_document.file.read(), 'application/pdf')
        return attachment

    @property
    def postal_address_obj(self):
        if self.submitter_obj.postal_same_as_residential:
            address_obj = self.submitter_obj.residential_address
        else:
            address_obj = self.submitter_obj.postal_address
        return address_obj

    @property
    def proposal_applicant(self):
        proposal_applicant = None
        if self.current_proposal:
            proposal_applicant = self.current_proposal.proposal_applicant
        return proposal_applicant

    @property
    def postal_first_name(self):
        try:
            ret_value = self.proposal_applicant.first_name
        except:
            logger.error(f'Postal address first_name cannot be retrieved for the approval [{self}].')
            return ''

        if not ret_value:
            logger.warning(f'Empty postal_first_name found for the Approval: [{self}].')

        return ret_value

    @property
    def postal_last_name(self):
        try:
            ret_value = self.proposal_applicant.last_name
        except:
            logger.error(f'Postal address last_name cannot be retrieved for the approval [{self}].')
            return ''

        if not ret_value:
            logger.warning(f'Empty postal_last_name found for the Approval: [{self}].')

        return ret_value


    @property
    def postal_address_line1(self):
        try:
            ret_value = self.proposal_applicant.postal_address_line1
        except:
            logger.error(f'Postal address line1 cannot be retrieved for the approval [{self}].')
            return ''

        if not ret_value:
            logger.warning(f'Empty postal_address_line1 found for the Approval: [{self}].')

        return ret_value

    @property
    def postal_address_line2(self):
        try:
            ret_value = self.proposal_applicant.postal_address_line2
        except:
            logger.error(f'Postal address line2 cannot be retrieved for the approval [{self}]')
            return ''

        return ret_value

    @property
    def postal_address_state(self):
        try:
            ret_value = self.proposal_applicant.postal_address_state
        except:
            logger.error(f'Postal address state cannot be retrieved for the approval [{self}]')
            return ''

        if not ret_value:
            logger.warning(f'Empty state found for the postal address of the Approval: [{self}].')

        return ret_value

    @property
    def postal_address_suburb(self):
        try:
            ret_value = self.proposal_applicant.postal_address_suburb
        except:
            logger.error(f'Postal address locality cannot be retrieved for the approval [{self}]')
            return ''

        if not ret_value:
            logger.warning(f'Empty locality found for the postal address of the Approval: [{self}].')

        return ret_value

    @property
    def postal_address_postcode(self):
        try:
            ret_value = self.proposal_applicant.postal_address_postcode
        except:
            logger.error(f'Postal address postcode cannot be retrieved for the approval [{self}]')
            return ''

        if not ret_value:
            logger.warning(f'Empty postcode found for the postal address of the Approval: [{self}].')

        return ret_value

    @property
    def description(self):
        if hasattr(self, 'child_obj'):
            return self.child_obj.description
        return ''

    def write_approval_history(self, reason=None):
        logger.info(f'Writing the approval history of the approval: [{self}]...')

        history_count = self.approvalhistory_set.count()
        if not history_count:
            new_approval_history_entry = ApprovalHistory.objects.create(
                vessel_ownership=self.current_proposal.vessel_ownership,
                approval=self,
                proposal=self.current_proposal,
                start_date=self.issue_date,
                approval_letter=self.licence_document,
                reason='New application {}'.format(str(self.current_proposal)),
            )
        elif reason:
            new_approval_history_entry = ApprovalHistory.objects.create(
                vessel_ownership=self.current_proposal.vessel_ownership,
                approval=self,
                proposal=self.current_proposal,
                start_date=self.issue_date,
                approval_letter=self.licence_document,
                reason=reason,
            )
        else:
            new_approval_history_entry = ApprovalHistory.objects.create(
                vessel_ownership=self.current_proposal.vessel_ownership,
                approval=self,
                proposal=self.current_proposal,
                start_date=self.issue_date,
                approval_letter=self.licence_document,
            )

        # Move this logic to the 'export_and_email_sticker_data' cron job
        # stickers = self.stickers.filter(status__in=['ready', 'current', 'awaiting_printing'])
        # for sticker in stickers:
        #     new_approval_history_entry.stickers.add(sticker)

        approval_history = self.approvalhistory_set.all()
        ## rewrite history
        # current_proposal.previous_application must be set on renewal/amendment
        if self.current_proposal.previous_application:
            previous_application = self.current_proposal.previous_application
            qs = self.approvalhistory_set.filter(proposal=previous_application)
            if qs:
                # previous history entry exists
                end_date = self.issue_date
                previous_history_entry = self.approvalhistory_set.filter(proposal=previous_application)[0]
                # check vo sale date
                if previous_history_entry.vessel_ownership and previous_history_entry.vessel_ownership.end_date:
                    end_date = previous_history_entry.vessel_ownership.end_date
                # update previous_history_entry
                previous_history_entry.end_date = end_date
                previous_history_entry.save()

                if self.current_proposal.proposal_type.code == PROPOSAL_TYPE_AMENDMENT and self.current_proposal.vessel_ownership == self.current_proposal.previous_application.vessel_ownership:
                    # When renewal and vessel not changed
                    # Copy stickers from previous history to new history
                    for sticker in previous_history_entry.stickers.all():
                        new_approval_history_entry.stickers.add(sticker)

        # TODO: need to worry about all entries for this approval?
        ## reason
        return new_approval_history_entry

    def add_vessel_ownership(self, vessel_ownership):
        logger.info(f'Adding a vessel_ownership: [{vessel_ownership}]...')

        if not vessel_ownership:
            return None, False

        # do not add if this vessel_ownership already exists for the approval 
        vessel_ownership_on_approval = None
        created = None
        if not self.vesselownershiponapproval_set.filter(vessel_ownership=vessel_ownership):
            vessel_ownership_on_approval, created = VesselOwnershipOnApproval.objects.update_or_create(
                    vessel_ownership=vessel_ownership,
                    approval=self,
                    )
            if created:
                logger.info(f'New VesselOwnershipOnApproval: [{vessel_ownership_on_approval}] has been created with the VesselOwnership: [{vessel_ownership}] and Approval: [{self}].')
        return vessel_ownership_on_approval, created

    def add_mooring(self, mooring, site_licensee):
        # do not add if this mooring already exists for the approval
        target_date=datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()
        query = Q()
        query &= Q(mooring=mooring)
        query &= (Q(end_date__gt=target_date) | Q(end_date__isnull=True))  # Not ended yet

        created = None
        if not self.mooringonapproval_set.filter(query):
            mooring_on_approval, created = MooringOnApproval.objects.update_or_create(
                mooring=mooring,
                approval=self,
                site_licensee=site_licensee
            )
            if created:
                logger.info('New Mooring {} has been added to the approval {}'.format(mooring.name, self.lodgement_number))
        else:
            logger.warning(f'There is already a current MooringOnApproval object whose approval: [{self}], mooring: [{mooring}] and site_licensee: [{site_licensee}].')

    @property
    def bpay_allowed(self):
        if self.org_applicant:
            return self.org_applicant.bpay_allowed
        return False

    @property
    def monthly_invoicing_allowed(self):
        if self.org_applicant:
            return self.org_applicant.monthly_invoicing_allowed
        return False

    @property
    def monthly_invoicing_period(self):
        if self.org_applicant:
            return self.org_applicant.monthly_invoicing_period
        return None

    @property
    def monthly_payment_due_period(self):
        if self.org_applicant:
            return self.org_applicant.monthly_payment_due_period
        return None

    @property
    def applicant(self):
        # if self.org_applicant:
        #     return self.org_applicant.organisation.name
        # elif self.proxy_applicant:
        #     applicant = retrieve_email_userro(self.proxy_applicant)
        #     return "{} {}".format(
        #         applicant.first_name,
        #         applicant.last_name)
        # else:
        #     try:
        #         submitter = retrieve_email_userro(self.submitter)
        #         return "{} {}".format(
        #             submitter.first_name,
        #             submitter.last_name)
        #     except:
        #         return "Applicant Not Set"
        applicant = ''
        try:
            if self.current_proposal and self.current_proposal.proposal_applicant:
                applicant = f'{self.current_proposal.proposal_applicant.first_name} {self.current_proposal.proposal_applicant.last_name}'
        except Exception as e:
            logger.error(e)

        return applicant

    @property
    def linked_applications(self):
        ids = Proposal.objects.filter(approval__lodgement_number=self.lodgement_number).values_list('id', flat=True)
        all_linked_ids = Proposal.objects.filter(Q(previous_application__in=ids) | Q(id__in=ids)).values_list('lodgement_number', flat=True)
        return all_linked_ids

    @property
    def applicant_type(self):
        if self.org_applicant:
            return "org_applicant"
        elif self.proxy_applicant:
            return "proxy_applicant"
        else:
            return "submitter"

    @property
    def is_org_applicant(self):
        return True if self.org_applicant else False

    @property
    def applicant_id(self):
        if self.org_applicant:
            return self.org_applicant.id
        elif self.proxy_applicant:
            # return self.proxy_applicant.id
            return self.proxy_applicant
        else:
            # return self.submitter.id
            return self.submitter

    @property
    def title(self):
        return self.current_proposal.title

    @property
    def next_id(self):
        ids = map(int, [re.sub('^[A-Za-z]*', '', i) for i in Approval.objects.all().values_list('lodgement_number', flat=True) if i])
        ids = list(ids)
        return max(ids) + 1 if ids else 1

    def save(self, *args, **kwargs):
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(Approval, self).save(*args, **kwargs)
        self.child_obj.refresh_from_db()

        if type(self.child_obj) == MooringLicence and self.status in [
            Approval.APPROVAL_STATUS_EXPIRED,
            Approval.APPROVAL_STATUS_CANCELLED,
            Approval.APPROVAL_STATUS_SURRENDERED,
        ]:
            if type(self.child_obj) == MooringLicence and self.status in [
                Approval.APPROVAL_STATUS_CANCELLED,
                Approval.APPROVAL_STATUS_SURRENDERED,
            ]:
                current_stickers = self.stickers.filter(
                    status__in=[
                        # Sticker.STICKER_STATUS_CURRENT,
                        Sticker.STICKER_STATUS_AWAITING_PRINTING,
                        Sticker.STICKER_STATUS_NOT_READY_YET,
                        Sticker.STICKER_STATUS_READY,
                    ]
                )
                # When sticker is not printed yet, its status gets 'Cancelled'.
                for sticker in current_stickers:
                    sticker.status = Sticker.STICKER_STATUS_CANCELLED
                    sticker.save()
                    logger.info(f'Status: [{Sticker.STICKER_STATUS_CANCELLED}] has been set to the sticker: [{sticker}] due to the status: [{self.status}] of the approval: [{self}].')

                current_stickers = self.stickers.filter(
                    status__in=[
                        Sticker.STICKER_STATUS_CURRENT,
                    ]
                )
                # When sticker is current status, it should be returned.
                for sticker in current_stickers:
                    sticker.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
                    sticker.save()
                    logger.info(f'Status: [{Sticker.STICKER_STATUS_TO_BE_RETURNED}] has been set to the sticker: [{sticker}] due to the status: [{self.status}] of the approval: [{self}].')

            self.child_obj.update_auth_user_permits()

    def __str__(self):
        return f'{self.lodgement_number} {self.status}'

    @property
    def reference(self):
        return 'L{}'.format(self.id)

    @property
    def can_external_action(self):
        return self.status == Approval.APPROVAL_STATUS_CURRENT or self.status == Approval.APPROVAL_STATUS_SUSPENDED

    @property
    def can_reissue(self):
        return self.status == Approval.APPROVAL_STATUS_CURRENT or self.status == Approval.APPROVAL_STATUS_SUSPENDED

    @property
    def can_reinstate(self):
        return (self.status == Approval.APPROVAL_STATUS_CANCELLED or self.status == Approval.APPROVAL_STATUS_SUSPENDED or self.status == Approval.APPROVAL_STATUS_SURRENDERED) and self.can_action

    @property
    def allowed_assessors(self):
        return self.current_proposal.allowed_assessors

    def allowed_assessors_user(self, request):
        if self.current_proposal:
            return self.current_proposal.allowed_assessors_user(request)
        else:
            logger.warning(f'Current proposal of the approval: [{self}] not found.')
            return None

    def is_assessor(self, user):
        if isinstance(user, EmailUserRO):
            user = user.id

        if self.current_proposal:
            return self.current_proposal.is_assessor(user)
        else:
            logger.warning(f'Current proposal of the approval: [{self}] not found.')
            return False

    def is_approver(self,user):
        if isinstance(user, EmailUserRO):
            user = user.id

        if self.current_proposal:
            return self.current_proposal.is_approver(user)
        else:
            logger.warning(f'Current proposal of the approval: [{self}] not found.')
            return False

    @property
    def is_issued(self):
        return self.licence_number is not None and len(self.licence_number) > 0

    @property
    def can_action(self):
        if not (self.set_to_cancel or self.set_to_suspend or self.set_to_surrender):
            return True
        else:
            return False

    @property
    def code(self):
        return self.child_obj.code

    @property
    def amend_or_renew(self):
        try:
            if not self.status in [Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED, Approval.APPROVAL_STATUS_FULFILLED,]:
                return None
            amend_renew = 'amend'
            ## test whether any renewal or amendment applications have been created
            customer_status_choices = []
            ria_generated_proposal_qs = None
            if type(self.child_obj) == WaitingListAllocation:
                customer_status_choices = [Proposal.CUSTOMER_STATUS_WITH_ASSESSOR, Proposal.CUSTOMER_STATUS_DRAFT]
                ria_generated_proposal_qs = self.child_obj.ria_generated_proposal.filter(customer_status__in=customer_status_choices)
            else:
                if type(self.child_obj) == AnnualAdmissionPermit:
                    customer_status_choices = [Proposal.CUSTOMER_STATUS_WITH_ASSESSOR, Proposal.CUSTOMER_STATUS_DRAFT, Proposal.CUSTOMER_STATUS_PRINTING_STICKER]
                elif type(self.child_obj) == AuthorisedUserPermit:
                    customer_status_choices = [Proposal.CUSTOMER_STATUS_WITH_ASSESSOR, Proposal.CUSTOMER_STATUS_DRAFT, Proposal.CUSTOMER_STATUS_AWAITING_ENDORSEMENT, Proposal.CUSTOMER_STATUS_PRINTING_STICKER, Proposal.CUSTOMER_STATUS_AWAITING_PAYMENT,]
                elif type(self.child_obj) == MooringLicence:
                    customer_status_choices = [Proposal.CUSTOMER_STATUS_WITH_ASSESSOR, Proposal.CUSTOMER_STATUS_DRAFT, Proposal.CUSTOMER_STATUS_AWAITING_ENDORSEMENT, Proposal.CUSTOMER_STATUS_PRINTING_STICKER, Proposal.CUSTOMER_STATUS_AWAITING_PAYMENT, Proposal.CUSTOMER_STATUS_AWAITING_DOCUMENTS]
            existing_proposal_qs=self.proposal_set.filter(customer_status__in=customer_status_choices,
                    proposal_type__in=ProposalType.objects.filter(code__in=[PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_SWAP_MOORINGS,]))
            ## cannot amend or renew
            if existing_proposal_qs or ria_generated_proposal_qs:
                amend_renew = None
            ## If Approval has been set for renewal, this takes priority
            elif self.renewal_document and self.renewal_sent:
                amend_renew = 'renew'
            return amend_renew
        except Exception as e:
            raise e

    @property
    def mooring_swappable(self):
        # logger.debug(f'approval: [{self}]')
        # logger.debug(f'amend_or_renew: [{self.amend_or_renew}]')
        try:
            if self.amend_or_renew:
                return True  # if it is amendable/renewable, it is also swappable.
            return False
        except Exception as e:
            raise e

    def generate_doc(self, preview=False):
        if preview:
            from mooringlicensing.doctopdf import create_approval_doc_bytes
            return create_approval_doc_bytes(self)

        self.licence_document = create_approval_doc(self)  # Update the attribute to the latest doc

        self.save(version_comment='Created Approval PDF: {}'.format(self.licence_document.name))
        self.current_proposal.save(version_comment='Created Approval PDF: {}'.format(self.licence_document.name))
        logger.info(f'Licence document: [{self.licence_document._file.url}] for the approval: [{self}] has been created.')

        if hasattr(self, 'approval') and self.approval:
            self.approval.licence_document = self.licence_document
            self.approval.save()

    def generate_au_summary_doc(self, user):
        from mooringlicensing.doctopdf import create_authorised_user_summary_doc_bytes
        target_date=datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()

        if hasattr(self, 'mooring'):
            query = Q()
            query &= Q(mooring=self.mooring)
            query &= Q(approval__status__in=[Approval.APPROVAL_STATUS_SUSPENDED, Approval.APPROVAL_STATUS_CURRENT,])
            query &= Q(Q(end_date__gt=target_date) | Q(end_date__isnull=True))
            moa_set = MooringOnApproval.objects.filter(query)
            if moa_set.count() > 0:
                # Authorised User exists
                contents_as_bytes = create_authorised_user_summary_doc_bytes(self)

                filename = 'authorised-user-summary-{}.pdf'.format(self.lodgement_number)
                document = AuthorisedUserSummaryDocument.objects.create(approval=self, name=filename)

                # Save the bytes to the disk
                document._file.save(filename, ContentFile(contents_as_bytes), save=True)
                logger.info(f'Authorised User Summary document: [{filename}] has been created.')

                self.authorised_user_summary_document = document  # Update to the latest doc
                self.save(version_comment='Created Authorised User Summary PDF: {}'.format(self.authorised_user_summary_document.name))
                self.current_proposal.save(version_comment='Created Authorised User Summary PDF: {}'.format(self.authorised_user_summary_document.name))

    def generate_renewal_doc(self):
        self.renewal_document = create_renewal_doc(self, self.current_proposal)
        self.save(version_comment='Created Renewal PDF: {}'.format(self.renewal_document.name))
        self.current_proposal.save(version_comment='Created Renewal PDF: {}'.format(self.renewal_document.name))

    def log_user_action(self, action, request=None):
        if request:
            return ApprovalUserAction.log_action(self, action, request.user)
        else:
            return ApprovalUserAction.log_action(self, action)

    def expire_approval(self, user):
        with transaction.atomic():
            try:
                today = timezone.localtime(timezone.now()).date()
                if self.status == Approval.APPROVAL_STATUS_CURRENT and self.expiry_date < today:
                    self.status = Approval.APPROVAL_STATUS_EXPIRED
                    self.save()
                    send_approval_expire_email_notification(self)
                    proposal = self.current_proposal
                    ApprovalUserAction.log_action(self, ApprovalUserAction.ACTION_EXPIRE_APPROVAL.format(self.id), user)
                    ProposalUserAction.log_action(proposal, ProposalUserAction.ACTION_EXPIRED_APPROVAL_.format(proposal.id), user)
            except:
                raise

    def approval_cancellation(self, request, details):
        logger.debug(f'in approval_cancellation().  self: [{self}] details: [{details}]')
        with transaction.atomic():
            try:
                logger.info(f'Cancelling the approval: [{self}]...')
                if not request.user in self.allowed_assessors:
                    raise ValidationError('You do not have access to cancel this approval')
                if not self.can_reissue and self.can_action:
                    raise ValidationError('You cannot cancel approval if it is not current or suspended')

                cancellation_date = details.get('cancellation_date').strftime('%Y-%m-%d')
                cancellation_date = datetime.datetime.strptime(cancellation_date,'%Y-%m-%d').date()
                self.cancellation_date = cancellation_date
                self.cancellation_details = details.get('cancellation_details')
                today = timezone.now().date()
                if cancellation_date <= today:
                    if not self.status == Approval.APPROVAL_STATUS_CANCELLED:
                        self.status = Approval.APPROVAL_STATUS_CANCELLED
                        self.set_to_cancel = False
                        self.save()
                        logger.info(f'Status: [{Approval.APPROVAL_STATUS_CANCELLED}] has been set to the approval: [{self}]')

                        send_approval_cancel_email_notification(self)
                else:
                    self.set_to_cancel = True
                    self.save()
                    logger.info(f'True has been set to the "set_to_cancel" attribute of the approval: [{self}]')

                if type(self.child_obj) == WaitingListAllocation:
                    self.child_obj.processes_after_cancel()
                # Log proposal action
                self.log_user_action(ApprovalUserAction.ACTION_CANCEL_APPROVAL.format(self.id),request)
                # Log entry for organisation
                self.current_proposal.log_user_action(ProposalUserAction.ACTION_CANCEL_APPROVAL.format(self.current_proposal.id),request)
            except:
                raise

    def approval_suspension(self,request,details):
        with transaction.atomic():
            try:
                if not request.user in self.allowed_assessors:
                    raise ValidationError('You do not have access to suspend this approval')
                if not self.can_reissue and self.can_action:
                    raise ValidationError('You cannot suspend approval if it is not current or suspended')
                if details.get('to_date'):
                    to_date= details.get('to_date').strftime('%d/%m/%Y')
                else:
                    to_date=''
                self.suspension_details = {
                    'from_date' : details.get('from_date').strftime('%d/%m/%Y'),
                    'to_date' : to_date,
                    'details': details.get('suspension_details'),
                }
                today = timezone.now().date()
                from_date = datetime.datetime.strptime(self.suspension_details['from_date'],'%d/%m/%Y')
                from_date = from_date.date()
                if from_date <= today:
                    if not self.status == Approval.APPROVAL_STATUS_SUSPENDED:
                        self.status = Approval.APPROVAL_STATUS_SUSPENDED
                        self.set_to_suspend = False
                        self.save()
                        send_approval_suspend_email_notification(self)
                else:
                    self.set_to_suspend = True
                self.save()
                if type(self.child_obj) == MooringLicence:
                    self.child_obj.update_auth_user_permits()
                # Log approval action
                self.log_user_action(ApprovalUserAction.ACTION_SUSPEND_APPROVAL.format(self.id),request)
                # Log entry for proposal
                self.current_proposal.log_user_action(ProposalUserAction.ACTION_SUSPEND_APPROVAL.format(self.current_proposal.id),request)
            except:
                raise

    def reinstate_approval(self,request):
        with transaction.atomic():
            try:
                if not request.user in self.allowed_assessors:
                    raise ValidationError('You do not have access to reinstate this approval')
                if not self.can_reinstate:
                    raise ValidationError('You cannot reinstate approval at this stage')
                today = timezone.now().date()
                if not self.can_reinstate and self.expiry_date>= today:
                    raise ValidationError('You cannot reinstate approval at this stage')
                if self.status == Approval.APPROVAL_STATUS_CANCELLED:
                    self.cancellation_details =  ''
                    self.cancellation_date = None
                if self.status == Approval.APPROVAL_STATUS_SURRENDERED:
                    self.surrender_details = {}
                if self.status == Approval.APPROVAL_STATUS_SUSPENDED:
                    self.suspension_details = {}
                previous_status = self.status
                self.status = Approval.APPROVAL_STATUS_CURRENT
                self.save()
                if type(self.child_obj) == WaitingListAllocation and previous_status in [Approval.APPROVAL_STATUS_CANCELLED, Approval.APPROVAL_STATUS_SURRENDERED]:
                    wla = self.child_obj
                    wla.internal_status = Approval.INTERNAL_STATUS_WAITING
                    wla.save()
                    wla.set_wla_order()
                send_approval_reinstate_email_notification(self, request)
                # Log approval action
                self.log_user_action(ApprovalUserAction.ACTION_REINSTATE_APPROVAL.format(self.id),request)
                # Log entry for proposal
                self.current_proposal.log_user_action(ProposalUserAction.ACTION_REINSTATE_APPROVAL.format(self.current_proposal.id),request)
            except:
                raise

    def approval_surrender(self,request,details):
        with transaction.atomic():
            try:
                # Check if organisations the request.user belongs to include the organisation this application is for.
                # if not request.user.mooringlicensing_organisations.filter(organisation_id = self.applicant_id):
                orgs = Organisation.objects.filter(delegates__contains=[request.user.id,])  # These are the organisations request.user belongs to
                if orgs:
                    if self.org_applicant:
                        if not self.org_applicant.id in [org.id for org in orgs]:
                            if request.user not in self.allowed_assessors and not is_customer(request):
                                raise ValidationError('You do not have access to surrender this approval')
                if not self.can_reissue and self.can_action:
                    raise ValidationError('You cannot surrender approval if it is not current or suspended')
                self.surrender_details = {
                    'surrender_date' : details.get('surrender_date').strftime('%d/%m/%Y'),
                    'details': details.get('surrender_details'),
                }
                today = timezone.now().date()
                surrender_date = datetime.datetime.strptime(self.surrender_details['surrender_date'],'%d/%m/%Y')
                surrender_date = surrender_date.date()
                if surrender_date <= today:
                    if not self.status == Approval.APPROVAL_STATUS_SURRENDERED:
                        self.status = Approval.APPROVAL_STATUS_SURRENDERED
                        self.set_to_surrender = False
                        self.save()
                        send_approval_surrender_email_notification(self)
                else:
                    self.set_to_surrender = True
                    send_approval_surrender_email_notification(self, already_surrendered=False)
                self.save()
                if type(self.child_obj) == WaitingListAllocation:
                    self.child_obj.processes_after_cancel()
                # Log approval action
                self.log_user_action(ApprovalUserAction.ACTION_SURRENDER_APPROVAL.format(self.id),request)
                # Log entry for proposal
                self.current_proposal.log_user_action(ProposalUserAction.ACTION_SURRENDER_APPROVAL.format(self.current_proposal.id),request)
            except:
                raise

    # required to clean db of approvals with no child objs
    @property
    def no_child_obj(self):
        no_child_obj = True
        if hasattr(self, 'waitinglistallocation'):
            no_child_obj = False
        elif hasattr(self, 'annualadmissionpermit'):
            no_child_obj = False
        elif hasattr(self, 'authoriseduserpermit'):
            no_child_obj = False
        elif hasattr(self, 'mooringlicence'):
            no_child_obj = False
        return no_child_obj

    @property
    def child_obj(self):
        if hasattr(self, 'waitinglistallocation'):
            return self.waitinglistallocation
        elif hasattr(self, 'annualadmissionpermit'):
            return self.annualadmissionpermit
        elif hasattr(self, 'authoriseduserpermit'):
            return self.authoriseduserpermit
        elif hasattr(self, 'mooringlicence'):
            return self.mooringlicence
        else:
            raise ObjectDoesNotExist("Approval must have an associated child object - WLA, AAP, AUP or ML")

    @classmethod
    def approval_types_dict(cls, include_codes=[]):
        type_list = []
        for approval_type in Approval.__subclasses__():
            if hasattr(approval_type, 'code'):
                if approval_type.code in include_codes:
                    type_list.append({
                        "code": approval_type.code,
                        "description": approval_type.description,
                    })

        return type_list

    def get_fee_items(self):
        fee_items = []
        for proposal in self.proposal_set.all():
            logger.info(f'proposal: [{proposal}], proposal.fee_season: [{proposal.fee_season}]')
            for application_fee in proposal.application_fees.all():
                if application_fee.fee_items:
                    for fee_item in application_fee.fee_items.all():
                        fee_items.append(fee_item)
                else:
                    # Should not reach here, however the data generated at the early stage of the development may reach here.
                    logger.error('ApplicationFee: {} does not have any fee_item.  It should have at least one.')
        return fee_items

    @property
    def latest_applied_season(self):
        latest_applied_season = None

        if self.get_fee_items():
            for fee_item in self.get_fee_items():
                if latest_applied_season:
                    if latest_applied_season.end_date < fee_item.fee_period.fee_season.end_date:
                        latest_applied_season = fee_item.fee_period.fee_season
                else:
                    latest_applied_season = fee_item.fee_period.fee_season
        else:
            logger.info(f'No FeeItems found under the Approval: {self}.  Probably because the approval is AUP and the ML for the same vessel exists.')
            for proposal in self.proposal_set.all():
                if proposal.fee_season:
                    if latest_applied_season:
                        if latest_applied_season.end_date < proposal.fee_season.end_date:
                            latest_applied_season = proposal.fee_season
                    else:
                        latest_applied_season = proposal.fee_season

        return latest_applied_season

    def _update_status_of_sticker_to_be_removed(self, stickers_to_be_removed, stickers_to_be_replaced_for_renewal=[]):
        for sticker in stickers_to_be_removed:
            if sticker.status in [Sticker.STICKER_STATUS_CURRENT, Sticker.STICKER_STATUS_AWAITING_PRINTING,]:
                if sticker in stickers_to_be_replaced_for_renewal:
                    # For renewal, old sticker is still in 'current' status until new sticker gets 'current' status
                    # When new sticker gets 'current' status, old sticker gets 'expired' status
                    pass
                else:
                    sticker.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
                    sticker.save()
                    logger.info(f'Sticker: [{sticker}] status has been changed to [{sticker.status}]')
            elif sticker.status == Sticker.STICKER_STATUS_TO_BE_RETURNED:
                # Do nothing
                pass
            elif sticker.status in [Sticker.STICKER_STATUS_READY,]:
                # These sticker objects were created, but not sent to the printing company
                # So we just make it 'cancelled'
                if sticker.number:
                    # Should not reach here.
                    # But if this is the case, we assign 'cancelled' status so that it is shown in the sticker table.
                    sticker.status = Sticker.STICKER_STATUS_CANCELLED
                else:
                    sticker.status = Sticker.STICKER_STATUS_NOT_READY_YET  # This sticker object was created, but no longer needed before being printed.
                                                                           # Therefore, assign not_ready_yet status not to be picked up for printing
                sticker.save()
                logger.info(f'Sticker: [{sticker}] status has been changed to [{sticker.status}]')
            else:
                # Do nothing
                pass

    def manage_stickers(self, proposal=None):
        return self.child_obj.manage_stickers(proposal)


class WaitingListAllocation(Approval):
    approval = models.OneToOneField(Approval, parent_link=True, on_delete=models.CASCADE)
    code = 'wla'
    prefix = 'WLA'
    description = 'Waiting List Allocation'
    template_file_key = GlobalSettings.KEY_WLA_TEMPLATE_FILE

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    @staticmethod
    def get_intermediate_approvals(email_user_id):
        approvals = WaitingListAllocation.objects.filter(submitter=email_user_id).exclude(status__in=[
            Approval.APPROVAL_STATUS_CANCELLED,
            Approval.APPROVAL_STATUS_EXPIRED,
            Approval.APPROVAL_STATUS_SURRENDERED,
            Approval.APPROVAL_STATUS_FULFILLED,]).exclude(internal_status__in=[
            Approval.INTERNAL_STATUS_OFFERED,
            Approval.INTERNAL_STATUS_SUBMITTED,
        ])
        return approvals

    def get_grace_period_end_date(self):
        end_date = None
        if self.current_proposal and self.current_proposal.vessel_ownership and self.current_proposal.vessel_ownership.end_date:
            end_date = self.current_proposal.vessel_ownership.end_date + relativedelta(months=+6)
        return end_date

    def get_context_for_licence_permit(self):
        try:
            # v_details = self.current_proposal.vessel_details
            v_details = self.current_proposal.latest_vessel_details
            v_ownership = self.current_proposal.vessel_ownership
            if v_details and not v_ownership.end_date:
                vessel_rego_no = v_details.vessel.rego_no
                vessel_name = v_details.vessel_name
                vessel_length = v_details.vessel_applicable_length
                vessel_draft = v_details.vessel_draft
            else:
                vessel_rego_no = ''
                vessel_name = ''
                vessel_length = ''
                vessel_draft = ''

            # Return context for the licence/permit document
            from mooringlicensing.ledger_api_utils import retrieve_email_userro
            # submitter = retrieve_email_userro(self.submitter)
            context = {
                'approval': self,
                'application': self.current_proposal,
                'issue_date': self.issue_date.strftime('%d/%m/%Y'),
                # 'applicant_name': self.submitter.get_full_name(),
                # 'applicant_full_name': self.submitter.get_full_name(),
                'applicant_name': self.current_proposal.proposal_applicant.get_full_name(),
                'applicant_full_name': self.current_proposal.proposal_applicant.get_full_name(),
                'bay_name': self.current_proposal.preferred_bay.name,
                'allocation_date': self.wla_queue_date.strftime('%d/%m/%Y'),
                'position_number': self.wla_order,
                'vessel_rego_no': vessel_rego_no,
                'vessel_name': vessel_name,
                'vessel_length': vessel_length,
                'vessel_draft': vessel_draft,
                'public_url': get_public_url(),
            }
            return context
        except Exception as e:
            msg = 'Waiting List Allocation: {} cannot generate document. {}'.format(self.lodgement_number, str(e))
            logger.error(msg)
            raise e

    def save(self, *args, **kwargs):
        if self.lodgement_number == '':
            self.lodgement_number = self.prefix + '{0:06d}'.format(self.next_id)
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(Approval, self).save(*args, **kwargs)

    def manage_stickers(self, proposal):
        # No stickers for WL
        proposal.processing_status = Proposal.PROCESSING_STATUS_APPROVED
        proposal.save()
        return None, None

    def set_wla_order(self):
        from mooringlicensing.components.main.utils import reorder_wla
        """
        Renumber all the related allocations with 'current'/'suspended' status from #1 to #n
        """
        logger.info(f'Ordering the allocations for the Waiting List Allocation: [{self}], bay: [{self.current_proposal.preferred_bay}]...')
        reorder_wla(self.current_proposal.preferred_bay)
        self.refresh_from_db()  # Should be self.proposal.refresh_from_db()???
        return self

    def processes_after_cancel(self):
        self.internal_status = None
        self.status = Approval.APPROVAL_STATUS_CANCELLED  # Cancelled has been probably set before reaching here.
        # self.wla_queue_date = None
        self.wla_order = None
        self.save()
        logger.info(f'Set attributes as follows: [internal_status=None, status=cancelled, wla_order=None] of the WL Allocation: [{self}].')
        self.set_wla_order()

    def process_after_approval(self):
        # self.internal_status = 'approved'
        self.internal_status = Approval.INTERNAL_STATUS_APPROVED
        self.status = Approval.APPROVAL_STATUS_FULFILLED
        self.wla_order = None
        self.save()
        logger.info(f'Set attributes as follows: [internal_status=approved, status=fulfilled, wla_order=None] of the WL Allocation: [{self}].')
        self.set_wla_order()
    
    def process_after_withdrawn(self):
        self.wla_order = None
        self.internal_status = Approval.INTERNAL_STATUS_WAITING
        self.save()
        logger.info(f'Set attributes as follows: [internal_status=waiting, wla_order=None] of the WL Allocation: [{self}].')
        self.set_wla_order()

    def process_after_discarded(self):
        self.wla_order = None
        self.status = Approval.APPROVAL_STATUS_FULFILLED  # ML application has been discarded, but in terms of WLAllocation perspective, it's regarded as 'fulfilled'.
        self.save()
        logger.info(f'Set attributes as follows: [status=fulfilled, wla_order=None] of the WL Allocation: [{self}].')
        self.set_wla_order()

    def reinstate_wla_order(self):
        """
        This function makes this WL allocation back to the 'waiting' status
        """
        self.wla_order = None
        self.status = Approval.APPROVAL_STATUS_CURRENT
        self.internal_status = Approval.INTERNAL_STATUS_WAITING
        self.save()
        logger.info(f'Set attributes as follows: [status=current, internal_status=waiting, wla_order=None] of the WL Allocation: [{self}].  These changes make this WL allocation back to the waiting list queue.')
        self.set_wla_order()
        return self


class AnnualAdmissionPermit(Approval):
    approval = models.OneToOneField(Approval, parent_link=True, on_delete=models.CASCADE)
    code = 'aap'
    prefix = 'AAP'
    description = 'Annual Admission Permit'
    sticker_colour = 'blue'
    template_file_key = GlobalSettings.KEY_AAP_TEMPLATE_FILE

    class Meta:
        app_label = 'mooringlicensing'

    def get_grace_period_end_date(self):
        # No grace period for the AAP
        return None

    def process_after_discarded(self):
        logger.debug(f'in AAP called.')

    def process_after_withdrawn(self):
        logger.debug(f'in AAP called.')

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    def get_context_for_licence_permit(self):
        try:
            # Return context for the licence/permit document
            v_details = self.current_proposal.latest_vessel_details
            v_ownership = self.current_proposal.vessel_ownership
            if v_details and not v_ownership.end_date:
                vessel_rego_no = v_details.vessel.rego_no
                vessel_name = v_details.vessel_name
                vessel_length = v_details.vessel_applicable_length
            else:
                vessel_rego_no = ''
                vessel_name = ''
                vessel_length = ''

            context = {
                'approval': self,
                'application': self.current_proposal,
                'issue_date': self.issue_date.strftime('%d/%m/%Y'),
                'applicant_name': self.current_proposal.proposal_applicant.get_full_name(),
                'p_address_line1': self.postal_address_line1,
                'p_address_line2': self.postal_address_line2,
                'p_address_suburb': self.postal_address_suburb,
                'p_address_state': self.postal_address_state,
                'p_address_postcode': self.postal_address_postcode,
                'vessel_rego_no': vessel_rego_no,
                'vessel_name': vessel_name,
                'vessel_length': vessel_length,
                'expiry_date': self.expiry_date.strftime('%d/%m/%Y'),
                'public_url': get_public_url(),
            }
            return context
        except Exception as e:
            msg = 'Annual Admission Permit: {} cannot generate permit. {}'.format(self.lodgement_number, str(e))
            logger.error(msg)
            raise e

    def save(self, *args, **kwargs):
        if self.lodgement_number == '':
            self.lodgement_number = self.prefix + '{0:06d}'.format(self.next_id)
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(Approval, self).save(*args, **kwargs)

    def _create_new_sticker_by_proposal(self, proposal, sticker_to_be_replaced=None):
        new_sticker = Sticker.objects.create(
            approval=self,
            # vessel_ownership=proposal.vessel_ownership,
            # fee_constructor=proposal.fee_constructor,
            vessel_ownership=proposal.vessel_ownership if proposal.vessel_ownership else sticker_to_be_replaced.vessel_ownership if sticker_to_be_replaced else None,
            fee_constructor=proposal.fee_constructor if proposal.fee_constructor else sticker_to_be_replaced.fee_constructor if sticker_to_be_replaced else None,
            proposal_initiated=proposal,
            fee_season=self.latest_applied_season,
        )

        logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')
        return new_sticker

    def _get_current_stickers(self):
        current_stickers = self.stickers.filter(
            status__in=[
                Sticker.STICKER_STATUS_CURRENT,
                Sticker.STICKER_STATUS_AWAITING_PRINTING,
            ]
        )
        return current_stickers

    def manage_stickers(self, proposal):
        logger.info(f'Managing stickers for the AnnualAdmissionPermit: [{self}]...')

        new_sticker = None
        existing_sticker_to_be_returned = None  # new_sticker.status=not_ready_yet, existing_sticker.status=to_be_returned
        existing_sticker_to_be_expired = None  # new_sticker.status=ready, new_sticker.sticker_to_replace=existing_sticker

        # Check if a new sticker needs to be created
        create_new_sticker = True
        if proposal.proposal_type.code == PROPOSAL_TYPE_AMENDMENT:
            if proposal.vessel_ownership == proposal.previous_application.vessel_ownership:
                next_colour = Sticker.get_vessel_size_colour_by_length(proposal.vessel_length)
                current_colour = Sticker.get_vessel_size_colour_by_length(proposal.previous_application.vessel_length)
                if current_colour == next_colour:
                    # This is the only case where there is no new sticker to be created
                    create_new_sticker = False

        if create_new_sticker:
            new_sticker_status = Sticker.STICKER_STATUS_READY  # default status is 'ready'

            if self.stickers.filter(status__in=[Sticker.STICKER_STATUS_READY, Sticker.STICKER_STATUS_NOT_READY_YET,]):
                # Should not reach here
                raise Exception('Cannot create a new sticker...  There is at least one sticker with ready/not_ready_yet status for the approval: [{self}].')

            # Handle the existing sticker if there is.
            existing_stickers = self.stickers.filter(status__in=[
                Sticker.STICKER_STATUS_CURRENT,
                Sticker.STICKER_STATUS_AWAITING_PRINTING,
                Sticker.STICKER_STATUS_TO_BE_RETURNED,
            ])
            existing_sticker = None
            if existing_stickers:
                existing_sticker = existing_stickers.order_by('number').last()  # There should be just one existing sticker
                if proposal.proposal_type.code == PROPOSAL_TYPE_AMENDMENT:
                    # There is an existing sticker to be replaced
                    if proposal.vessel_ownership == proposal.previous_application.vessel_ownership:
                        # Same vessel_ownership means the same vessel.  In the case no other vessel related, existing sticker does not need to be returned.
                        existing_sticker_to_be_expired = existing_sticker
                    else:
                        # Different vessel related.  In this case, existing sticker needs to be returned.
                        existing_sticker_to_be_returned = existing_sticker
                        new_sticker_status = Sticker.STICKER_STATUS_NOT_READY_YET
                elif proposal.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
                    # There is an existing sticker to be replaced
                    existing_sticker_to_be_expired = existing_sticker

            # Create a new sticker
            new_sticker = Sticker.objects.create(
                approval=self,
                vessel_ownership=proposal.vessel_ownership if proposal.vessel_ownership else existing_sticker.vessel_ownership if existing_sticker else None,
                fee_constructor=proposal.fee_constructor if proposal.fee_constructor else existing_sticker.fee_constructor if existing_sticker else None,
                proposal_initiated=proposal,
                fee_season=self.latest_applied_season,
                status=new_sticker_status,
            )
            logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')

            # Set statuses to both new and existing stickers
            if existing_sticker_to_be_expired:
                new_sticker.sticker_to_replace = existing_sticker_to_be_expired
                new_sticker.save()
            if existing_sticker_to_be_returned:
                existing_sticker_to_be_returned.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
                existing_sticker_to_be_returned.save()

        else:
            logger.info(f'No new sticker is going to be created because this is amendment application with the same vessel and the same sticker colour.')

        return new_sticker, existing_sticker_to_be_returned

    def _calc_stickers(self, proposal):
        # New sticker created with status Ready
        new_sticker = self._create_new_sticker_by_proposal(proposal)
        # Old sticker goes to status To be Returned
        current_stickers = self._get_current_stickers()
        for current_sticker in current_stickers:
            current_sticker.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
            current_sticker.save()
            logger.info(f'Status: [{Sticker.STICKER_STATUS_TO_BE_RETURNED}] has been set to the sticker {current_sticker}.')
        if current_stickers:
            if proposal.vessel_ownership == proposal.previous_application.vessel_ownership:
                # When the application does not change to new vessel,
                # it gets 'printing_sticker' status
                proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
                proposal.save()
                logger.info(f'Status: [{Proposal.PROCESSING_STATUS_PRINTING_STICKER}] has been set to the proposal {proposal}.')
            else:
                # When the application changes to new vessel
                # it gets 'sticker_to_be_returned' status
                new_sticker.status = Sticker.STICKER_STATUS_NOT_READY_YET
                new_sticker.save()
                logger.info(f'Status: [{Sticker.STICKER_STATUS_NOT_READY_YET}] has been set to the sticker {new_sticker}.')

                proposal.processing_status = Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED
                proposal.save()
                logger.info(f'Status: [{Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED}] has been set to the proposal {proposal}.')
        else:
            # Even when 'amendment' application, there might be no current stickers because of sticker-lost, etc
            proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            proposal.save()
            logger.info(f'Status: [{Proposal.PROCESSING_STATUS_PRINTING_STICKER}] has been set to the proposal {proposal}.')
        return [], list(current_stickers)


class AuthorisedUserPermit(Approval):
    approval = models.OneToOneField(Approval, parent_link=True, on_delete=models.CASCADE)
    code = 'aup'
    prefix = 'AUP'
    description = 'Authorised User Permit'
    sticker_colour = 'yellow'
    template_file_key = GlobalSettings.KEY_AUP_TEMPLATE_FILE

    class Meta:
        app_label = 'mooringlicensing'

    def get_grace_period_end_date(self):
        # No grace period for the AUP
        return None

    def process_after_discarded(self):
        logger.debug(f'in AUP called.')

    def process_after_withdrawn(self):
        logger.debug(f'in AUP called.')

    def get_authorised_by(self):
        preference = self.current_proposal.child_obj.get_mooring_authorisation_preference()
        return preference

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    def get_context_for_licence_permit(self):
        try:
            # Return context for the licence/permit document
            moorings = []
            for mooring in self.current_moorings:
                m = {}
                # calculate phone number(s)
                numbers = []
                if mooring.mooring_licence.submitter_obj.mobile_number:
                    numbers.append(mooring.mooring_licence.submitter_obj.mobile_number)
                elif mooring.mooring_licence.submitter_obj.phone_number:
                    numbers.append(mooring.mooring_licence.submitter_obj.phone_number)
                m['name'] = mooring.name
                m['licensee_full_name'] = mooring.mooring_licence.submitter_obj.get_full_name()
                m['licensee_email'] = mooring.mooring_licence.submitter_obj.email
                m['licensee_phone'] = ', '.join(numbers)
                moorings.append(m)

            v_details = self.current_proposal.latest_vessel_details
            v_ownership = self.current_proposal.vessel_ownership
            if v_details and not v_ownership.end_date:
                logger.info(f'VesselDetails: [{v_details}] are to be used as the context for the AU permit: [{self}].')
                vessel_rego_no = v_details.vessel.rego_no
                vessel_name = v_details.vessel_name
                vessel_length = v_details.vessel_applicable_length
                vessel_draft = v_details.vessel_draft
            else:
                logger.info(f'Null vessel is to be used as the context for the AU permit: [{self}].')
                vessel_rego_no = ''
                vessel_name = ''
                vessel_length = ''
                vessel_draft = ''

            context = {
                'approval': self,
                'application': self.current_proposal,
                'issue_date': self.issue_date.strftime('%d/%m/%Y') if self.issue_date else '',
                'applicant_name': self.current_proposal.proposal_applicant.get_full_name(),
                'p_address_line1': self.postal_address_line1,
                'p_address_line2': self.postal_address_line2,
                'p_address_suburb': self.postal_address_suburb,
                'p_address_state': self.postal_address_state,
                'p_address_postcode': self.postal_address_postcode,
                'vessel_rego_no': vessel_rego_no,
                'vessel_name': vessel_name,
                'vessel_length': vessel_length,
                'vessel_draft': vessel_draft,
                'moorings': moorings,  # m.name, m.licensee_full_name, m.licensee_email, m.licensee_phone
                'expiry_date': self.expiry_date.strftime('%d/%m/%Y') if self.expiry_date else '',
                'public_url': get_public_url(),
            }
            return context
        except Exception as e:
            msg = 'Authorised User Permit: {} cannot generate permit. {}'.format(self.lodgement_number, str(e))
            logger.error(msg)
            raise e

    def save(self, *args, **kwargs):
        if self.lodgement_number == '':
            self.lodgement_number = self.prefix + '{0:06d}'.format(self.next_id)
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(Approval, self).save(*args, **kwargs)

    def internal_reissue(self, mooring_licence=None):
        ## now reissue approval
        if self.current_proposal.vessel_ownership and not self.current_proposal.vessel_ownership.end_date:
            # When there is a current vessel
            self.current_proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            self.current_proposal.save()
        self.reissued=True
        self.save()
        # Create a log entry for the proposal and approval
        self.current_proposal.log_user_action(ProposalUserAction.ACTION_REISSUE_APPROVAL.format(self.lodgement_number))
        self.approval.log_user_action(ApprovalUserAction.ACTION_REISSUE_APPROVAL.format(self.lodgement_number))
        if mooring_licence:
            self.approval.log_user_action(ApprovalUserAction.ACTION_REISSUE_APPROVAL_ML.format(mooring_licence.lodgement_number))
        ## final approval
        self.current_proposal.final_approval()

    def update_moorings(self, mooring_licence):
        # The status of the mooring_licence should be in ['expired', 'cancelled', 'surrendered', 'suspended']
        if mooring_licence.status not in [Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,]:
            # Set end_date to the moa because the mooring on it is no longer available.
            moa = self.mooringonapproval_set.get(mooring__mooring_licence=mooring_licence)
            if not moa.end_date:
                moa.end_date = datetime.datetime.now().date()
                moa.save()
                logger.info(f'Set end_date: [{moa.end_date}] to the MooringOnApproval: [{moa}] because the Mooring: [{moa.mooring}] is no longer available.')

        if not self.mooringonapproval_set.filter(mooring__mooring_licence__status__in=[Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,]):
            ## No moorings left on this AU permit, include information that permit holder can amend and apply for new mooring up to expiry date.
            # send_auth_user_no_moorings_notification(self.approval)
            logger.info(f'There are no moorings left on the AU approval: [{self}].')
            send_auth_user_mooring_removed_notification(self.approval, mooring_licence)
        else:
            for moa in self.mooringonapproval_set.all():
                # There is at lease one mooring on this AU permit with 'current'/'suspended' status
                ## notify authorised user permit holder that the mooring is no longer available
                logger.info(f'There is still at least one mooring on this AU Permit: [{self}] with current/suspended status.')
                if moa.mooring == mooring_licence.mooring:
                    ## send email to auth user
                    send_auth_user_mooring_removed_notification(self.approval, mooring_licence)
        self.internal_reissue(mooring_licence)

    @property
    def current_moorings(self):
        moorings = []
        moas = self.mooringonapproval_set.filter(Q(end_date__isnull=True) & Q(mooring__mooring_licence__status=MooringLicence.APPROVAL_STATUS_CURRENT))
        for moa in moas:
            moorings.append(moa.mooring)
        return moorings


    def manage_stickers(self, proposal=None):
        logger.info(f'Managing stickers for the AuthorisedUserPermit: [{self}]...')

        # Lists to be returned to the caller
        moas_to_be_reallocated = []  # List of MooringOnApproval objects which are to be on the new stickers
        stickers_to_be_returned = []  # List of the stickers to be returned

        # Lists used only in this function
        _stickers_to_be_replaced = []  # List of the stickers to be replaced by the new stickers.
        _stickers_to_be_replaced_for_renewal = []  # Stickers in this list get 'expired' status.  When replaced for renewal, sticker doesn't need 'to be returned'.  This is used for that.

        # 1. Find all the moorings which should be assigned to the new stickers
        new_moas = MooringOnApproval.objects.filter(approval=self, sticker__isnull=True, end_date__isnull=True)  # New moa doesn't have stickers.
        for moa in new_moas:
            if moa not in moas_to_be_reallocated:
                if moa.approval and moa.approval.current_proposal and moa.approval.current_proposal.vessel_details:
                    # There is a vessel in this application
                    logger.info(f'Mooring: [{moa.mooring}] is assigned to the new sticker.')
                    moas_to_be_reallocated.append(moa)
                else:
                    # Null vessel
                    pass

        # 2. Find all the moas to be removed and update stickers_to_be_replaced
        moas_to_be_removed = MooringOnApproval.get_moas_to_be_removed_by_approval(self)
        for moa in moas_to_be_removed:
            _stickers_to_be_replaced.append(moa.sticker)

        # 3. Find all the moas to be replaced and update stickers_to_be_replaced
        if proposal and proposal.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
            # When Renewal/reissuedRenewal, (vessel changed, null vessel)
            # When renewal, all the current/awaiting_printing stickers to be replaced
            # All the sticker gets 'expired' status once payment made
            moas_current = MooringOnApproval.get_current_moas_by_approval(self)
            for moa in moas_current:
                if moa.sticker not in _stickers_to_be_replaced:
                    _stickers_to_be_replaced.append(moa.sticker)
                    _stickers_to_be_replaced_for_renewal.append(moa.sticker)

        # 4. Update lists due to the vessel changes
        moas_to_be_reallocated, _stickers_to_be_replaced = self.update_lists_due_to_vessel_changes(moas_to_be_reallocated, _stickers_to_be_replaced, proposal)

        # Find moas on the stickers which are to be replaced
        moas_to_be_reallocated, stickers_to_be_returned = self.update_lists_due_to_stickers_to_be_replaced(_stickers_to_be_replaced, moas_to_be_reallocated, moas_to_be_removed, stickers_to_be_returned)

        # We have to fill the existing unfilled sticker first.
        moas_to_be_reallocated, stickers_to_be_returned = self.check_unfilled_existing_sticker(moas_to_be_reallocated, stickers_to_be_returned, moas_to_be_removed)

        # There may be sticker(s) to be returned by record-sale
        # Rewrite???  Following codes pick up the stickers to be returened due not only to sale but other reasons... Is this OK???
        # stickers_return = proposal.approval.stickers.filter(status__in=[Sticker.STICKER_STATUS_TO_BE_RETURNED,])
        appr = proposal.approval if proposal else self
        stickers_return = appr.stickers.filter(status__in=[Sticker.STICKER_STATUS_TO_BE_RETURNED,])
        for sticker in stickers_return:
            stickers_to_be_returned.append(sticker)

        # Finally, assign mooring(s) to new sticker(s)
        self._assign_to_new_stickers(moas_to_be_reallocated, proposal, stickers_to_be_returned, _stickers_to_be_replaced_for_renewal)

        # Update statuses of stickers to be returned
        self._update_status_of_sticker_to_be_removed(stickers_to_be_returned, _stickers_to_be_replaced_for_renewal)

        return list(set(moas_to_be_reallocated)), list(set(stickers_to_be_returned))

    def update_lists_due_to_stickers_to_be_replaced(self, _stickers_to_be_replaced, moas_to_be_reallocated, moas_to_be_removed, stickers_to_be_returned):
        for sticker in _stickers_to_be_replaced:
            stickers_to_be_returned.append(sticker)
            for moa in sticker.mooringonapproval_set.all():
                # This sticker is possibly to be removed, but it may have current mooring(s)
                if moa not in moas_to_be_removed:
                    # This moa is not to be removed.  Therefore, it should be reallocated
                    moas_to_be_reallocated.append(moa)

        return list(set(moas_to_be_reallocated)), list(set(stickers_to_be_returned))

    def check_unfilled_existing_sticker(self, moas_to_be_reallocated, stickers_to_be_returned, moas_to_be_removed):
        if len(moas_to_be_reallocated) > 0:
            # There is at least one mooring to be allocated to a new sticker
            if len(moas_to_be_reallocated) == 1:
                logger.info(f'There is a mooring to be allocated to a new/existing sticker')
            else:
                logger.info(f'There are {len(moas_to_be_reallocated)} moorings to be allocated to a new/existing sticker')

            if len(moas_to_be_reallocated) % 4 != 0:
                # The number of moorings to be allocated is not a multiple of 4, which requires existing non-filled sticker to be replaced, too
                logger.info(f'The number of moorings: {len(moas_to_be_reallocated)} to be allocated is not a multiple of 4, which requires existing non-filled sticker to be replaced')

                # Find sticker which doesn't have 4 moorings on it
                stickers = self.stickers.filter(status__in=Sticker.STATUSES_AS_CURRENT).annotate(
                    num_of_moorings=Count('mooringonapproval')
                ).filter(num_of_moorings__lt=4)
                if stickers.count() == 1:
                    # There is one sticker found, which doesn't have 4 moorings
                    sticker = stickers.first()
                    logger.info(f'There is a non-filled sticker: [{sticker}], which is to be returned.')
                    stickers_to_be_returned.append(sticker)
                    for moa in sticker.mooringonapproval_set.all():
                        # moas_to_be_reallocated.append(moa)
                        if moa not in moas_to_be_removed:
                            moas_to_be_reallocated.append(moa)
                elif stickers.count() > 1:
                    # Should not reach here
                    msg = f'AUP: [{self.lodgement_number}] has more than one stickers without 4 moorings.'
                    logger.error(msg)
                    raise ValueError(msg)
            else:
                # Because the number of moorings to be on new stickers is a multiple of 4,
                # We just assign all of them to new stickers rather than finding out an existing sticker which is not filled with 4 moorings
                logger.info(f'The number of moorings: {len(moas_to_be_reallocated)} to be allocated is a multiple of 4.  We just assign all of them to new stickers rather than finding out an existing sticker which is not filled with 4 moorings.')

        return list(set(moas_to_be_reallocated)), list(set(stickers_to_be_returned))

    def update_lists_due_to_vessel_changes(self, moas_to_be_reallocated, stickers_to_be_replaced, proposal):
        if proposal and proposal.previous_application:
            # Check the sticker colour changes due to the vessel length change
            next_colour = Sticker.get_vessel_size_colour_by_length(proposal.vessel_length)
            current_colour = Sticker.get_vessel_size_colour_by_length(proposal.previous_application.vessel_length)
            if next_colour != current_colour:
                # Due to the vessel length change, the colour of the existing sticker needs to be changed
                moas_current = MooringOnApproval.get_current_moas_by_approval(self)
                for moa in moas_current:
                    stickers_to_be_replaced.append(moa.sticker)

        # Handle vessel changes
        if self.approval.current_proposal.vessel_removed:
            # self.current_proposal.vessel_ownership.vessel_removed --> All the stickers to be returned
            # A vessel --> No vessels
            # for sticker in stickers:
            #     stickers_to_be_replaced.append(sticker)
            moas_current = MooringOnApproval.get_current_moas_by_approval(self)
            for moa in moas_current:
                stickers_to_be_replaced.append(moa.sticker)

        if self.approval.current_proposal.vessel_swapped:
            # All the stickers to be removed and all the mooring on them to be reallocated
            # A vessel --> Another vessel
            # for sticker in stickers:
            #     stickers_to_be_replaced.append(sticker)
            moas_current = MooringOnApproval.get_current_moas_by_approval(self)
            for moa in moas_current:
                stickers_to_be_replaced.append(moa.sticker)

        if self.approval.current_proposal.vessel_null_to_new:
            # --> Create new sticker
            # No vessels --> New vessel
            # All moas should be on new stickers
            # moas_list = self.mooringonapproval_set. \
            #     filter(Q(end_date__isnull=True) & Q(mooring__mooring_licence__status__in=[MooringLicence.APPROVAL_STATUS_CURRENT,MooringLicence.APPROVAL_STATUS_SUSPENDED]))
            moas_list = MooringOnApproval.get_current_moas_by_approval(self)
            for moa in moas_list:
                moas_to_be_reallocated.append(moa)
        # Remove duplication
        moas_to_be_reallocated = list(set(moas_to_be_reallocated))
        stickers_to_be_replaced = list(set(stickers_to_be_replaced))

        return moas_to_be_reallocated, stickers_to_be_replaced

    def _assign_to_new_stickers(self, moas_to_be_on_new_sticker, proposal, stickers_to_be_returned, stickers_to_be_replaced_for_renewal=[]):
        logger.debug(f'moas_to_be_on_new_sticker: [{moas_to_be_on_new_sticker}]')
        logger.debug(f'proposal: [{proposal}]')
        logger.debug(f'stickers_to_be_returned: [{stickers_to_be_returned}]')
        logger.debug(f'stickers_to_be_replaced_for_renewal: [{stickers_to_be_replaced_for_renewal}]')
        new_stickers = []

        if len(stickers_to_be_returned):
            new_status = Sticker.STICKER_STATUS_READY
            for a_sticker in stickers_to_be_returned:
                if proposal and proposal.vessel_ownership:
                    # Current proposal has a vessel
                    if a_sticker.vessel_ownership.vessel.rego_no != proposal.vessel_ownership.vessel.rego_no:
                        new_status = Sticker.STICKER_STATUS_NOT_READY_YET  # This sticker gets 'ready' status once the sticker with 'to be returned' status is returned.
                        break
                else:
                    # Current proposal doesn't have a vessel
                    pass
        else:
            new_status = Sticker.STICKER_STATUS_READY

        new_sticker = None
        for moa_to_be_on_new_sticker in moas_to_be_on_new_sticker:
            logger.debug(f'moa_to_be_on_new_sticker: [{moa_to_be_on_new_sticker}]')
            if not new_sticker or new_sticker.mooringonapproval_set.count() % 4 == 0:
                # There is no stickers to fill, or there is a sticker but already be filled with 4 moas, create a new sticker
                new_sticker = Sticker.objects.create(
                    approval=self,
                    # vessel_ownership=moa_to_be_on_new_sticker.sticker.vessel_ownership if moa_to_be_on_new_sticker.sticker else proposal.vessel_ownership,
                    vessel_ownership=proposal.vessel_ownership if proposal and proposal.vessel_ownership else moa_to_be_on_new_sticker.sticker.vessel_ownership if moa_to_be_on_new_sticker.sticker else None,
                    fee_constructor=proposal.fee_constructor if proposal and proposal.fee_constructor else moa_to_be_on_new_sticker.sticker.fee_constructor if moa_to_be_on_new_sticker.sticker else None,
                    proposal_initiated=proposal,
                    fee_season=self.latest_applied_season,
                    status=new_status
                )
                logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')
                new_stickers.append(new_sticker)
            if moa_to_be_on_new_sticker.sticker in stickers_to_be_replaced_for_renewal:
                # Store old sticker in the new sticker in order to set 'expired' status to it once the new sticker gets 'awaiting_printing' status
                new_sticker.sticker_to_replace = moa_to_be_on_new_sticker.sticker
                new_sticker.save()
            # Update moa by a new sticker
            moa_to_be_on_new_sticker.sticker = new_sticker
            moa_to_be_on_new_sticker.save()

        return new_stickers


class MooringLicence(Approval):
    approval = models.OneToOneField(Approval, parent_link=True, on_delete=models.CASCADE)
    code = 'ml'
    prefix = 'MOL'
    description = 'Mooring Site Licence'
    sticker_colour = 'red'
    template_file_key = GlobalSettings.KEY_ML_TEMPLATE_FILE
    STATUSES_AS_CURRENT = [
        Approval.APPROVAL_STATUS_CURRENT,
        Approval.APPROVAL_STATUS_SUSPENDED,
    ]

    def _create_new_swap_moorings_application(self, request, new_mooring):
        new_proposal = self.current_proposal.clone_proposal_with_status_reset()
        new_proposal.proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_SWAP_MOORINGS)
        new_proposal.submitter = self.current_proposal.submitter
        new_proposal.previous_application = self.current_proposal
        new_proposal.keep_existing_vessel = True
        new_proposal.allocated_mooring = new_mooring  # Swap moorings here
        new_proposal.processing_status = Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS
        new_proposal.vessel_ownership = self.current_proposal.vessel_ownership
        
        # Copy vessel details
        new_proposal.vessel_details = self.current_proposal.vessel_details
        new_proposal.rego_no = self.current_proposal.rego_no
        new_proposal.vessel_id = self.current_proposal.vessel_id
        new_proposal.vessel_type = self.current_proposal.vessel_type
        new_proposal.vessel_name = self.current_proposal.vessel_name
        new_proposal.vessel_length = self.current_proposal.vessel_length
        new_proposal.vessel_draft = self.current_proposal.vessel_draft
        new_proposal.vessel_beam = self.current_proposal.vessel_beam
        new_proposal.vessel_weight = self.current_proposal.vessel_weight
        new_proposal.berth_mooring = self.current_proposal.berth_mooring
        new_proposal.dot_name = self.current_proposal.dot_name
        new_proposal.percentage = self.current_proposal.percentage
        new_proposal.individual_owner = self.current_proposal.individual_owner
        new_proposal.company_ownership_percentage = self.current_proposal.company_ownership_percentage
        new_proposal.company_ownership_name = self.current_proposal.company_ownership_name
        
        new_proposal.insurance_choice = self.current_proposal.insurance_choice
        new_proposal.silent_elector = self.current_proposal.silent_elector

        new_proposal.save(version_comment=f'New Swap moorings Application: [{new_proposal}] created with the new mooring: [{new_mooring}] from the origin {new_proposal.previous_application}')
        new_proposal.add_vessels_and_moorings_from_licence()

        self.current_proposal = new_proposal  # current_proposal of this ML should be new_proposal
        self.save()

        # Create a log entry for the proposal
        self.current_proposal.log_user_action(ProposalUserAction.ACTION_SWAP_MOORINGS_PROPOSAL.format(self.current_proposal.id), request)

        # Create a log entry for the approval
        self.log_user_action(ApprovalUserAction.ACTION_SWAP_MOORINGS.format(self.id), request)
        
        send_swap_moorings_application_created_notification(self, request)

    def swap_moorings(self, request, target_mooring_licence):
        logger.info(f'Swapping moorings between an approval: [{self} ({self.mooring})] and an approval: [{target_mooring_licence} ({target_mooring_licence.mooring})]...')

        with transaction.atomic():
            try:
                self._create_new_swap_moorings_application(request, target_mooring_licence.mooring)
                target_mooring_licence._create_new_swap_moorings_application(request, self.mooring)

            except Exception as e:
                raise e

    def get_grace_period_end_date(self):
        end_date = None
        today = datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()
        min_mooring_vessel_size_str = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINUMUM_MOORING_VESSEL_LENGTH).value
        min_mooring_vessel_size = float(min_mooring_vessel_size_str)

        for vooa in self.vesselownershiponapproval_set.all():
            vessel_ownership = vooa.vessel_ownership
            if vessel_ownership.vessel.latest_vessel_details.vessel_applicable_length >= min_mooring_vessel_size:
                # Vessel size is large enough
                if vessel_ownership.end_date is None or vessel_ownership.end_date >= today:
                    # The vessel has not been sold.  We don't have to consider the grace period.
                    end_date = None  # Reset end_date
                    break
                else:
                    # the vessel has been sold
                    if not end_date or end_date < vessel_ownership.end_date:
                        end_date = vessel_ownership.end_date + relativedelta(months=+6)
            else:
                # Vessel is too small to consider the grace period.  We are interested only in the vessels larger than or equal to the min_mooring_vessel_size.
                pass
        
        # logger.debug(f'end_date: {end_date} of ML: {self}')
        return end_date

    def process_after_withdrawn(self):
        logger.debug(f'in ML called.')

    def process_after_discarded(self):
        logger.debug(f'in ML called.')

    class Meta:
        app_label = 'mooringlicensing'

    @property
    def child_obj(self):
        raise NotImplementedError('This method cannot be called on a child_obj')

    @staticmethod
    def get_valid_approvals(email_user_id):
        approvals = MooringLicence.objects.filter(submitter=email_user_id).filter(status__in=[
            Approval.APPROVAL_STATUS_CURRENT,
            Approval.APPROVAL_STATUS_SUSPENDED,])
        return approvals

    def get_context_for_au_summary(self):
        if not hasattr(self, 'mooring'):
            # There should not be AuthorisedUsers for this ML
            return {}
        moa_set = MooringOnApproval.objects.filter(
            mooring=self.mooring,
            approval__status__in=[Approval.APPROVAL_STATUS_SUSPENDED, Approval.APPROVAL_STATUS_CURRENT,]
        )

        authorised_persons = []

        for moa in moa_set:
            authorised_person = {}
            if type(moa.approval.child_obj) == AuthorisedUserPermit:
                aup = moa.approval.child_obj
                authorised_by = aup.get_authorised_by()
                authorised_by = authorised_by.upper().replace('_', ' ')

                authorised_person['full_name'] = aup.current_proposal.proposal_applicant.get_full_name()
                authorised_person['vessel'] = {
                    'rego_no': aup.current_proposal.vessel_details.vessel.rego_no if aup.current_proposal.vessel_details else '',
                    'vessel_name': aup.current_proposal.vessel_details.vessel_name if aup.current_proposal.vessel_details else '',
                    'length': aup.current_proposal.vessel_details.vessel_applicable_length if aup.current_proposal.vessel_details else '',
                    'draft': aup.current_proposal.vessel_details.vessel_draft if aup.current_proposal.vessel_details else '',
                }
                authorised_person['authorised_date'] = aup.issue_date.strftime('%d/%m/%Y')
                authorised_person['authorised_by'] = authorised_by
                authorised_person['mobile_number'] = aup.current_proposal.proposal_applicant.mobile_number
                authorised_person['email_address'] = aup.current_proposal.proposal_applicant.email
                authorised_persons.append(authorised_person)

        today = datetime.datetime.now(pytz.timezone(settings.TIME_ZONE)).date()

        context = {
            'approval': self,
            'application': self.current_proposal,
            'issue_date': self.issue_date.strftime('%d/%m/%Y'),
            'applicant_first_name': retrieve_email_userro(self.submitter).first_name,
            'mooring_name': self.mooring.name,
            'authorised_persons': authorised_persons,
            'public_url': get_public_url(),
            'doc_generated_date': today.strftime('%d/%m/%Y'),
        }

        return context

    def get_context_for_licence_permit(self):
        try:
            #logger.info("self.issue_date: {}".format(self.issue_date))
            #logger.info("self.expiry_date: {}".format(self.expiry_date))
            # Return context for the licence/permit document
            licenced_vessel = None
            additional_vessels = []

            max_vessel_length = 0
            # for vessel in self.current_vessels:
            current_vessels = self.get_current_vessels_for_licence_doc()
            for vessel in current_vessels:
                v = {}
                v['vessel_rego_no'] = vessel['rego_no']
                v['vessel_name'] = vessel['latest_vessel_details'].vessel_name
                v['vessel_length'] = vessel['latest_vessel_details'].vessel_applicable_length
                v['vessel_draft'] = vessel['latest_vessel_details'].vessel_draft
                if not licenced_vessel:
                    # No licenced vessel stored yet
                    licenced_vessel = v
                else:
                    if licenced_vessel['vessel_length'] < v['vessel_length']:
                        # Found a larger vessel than the one stored as a licenced.  Replace it by the larger one.
                        additional_vessels.append(licenced_vessel)
                        licenced_vessel = v
                    else:
                        additional_vessels.append(v)

            context = {
                'approval': self,
                'application': self.current_proposal,
                'issue_date': self.issue_date.strftime('%d/%m/%Y'),
                'applicant_name': self.current_proposal.proposal_applicant.get_full_name(),
                'p_address_line1': self.postal_address_line1,
                'p_address_line2': self.postal_address_line2,
                'p_address_suburb': self.postal_address_suburb,
                'p_address_state': self.postal_address_state,
                'p_address_postcode': self.postal_address_postcode,
                'licenced_vessel': licenced_vessel,  # vessel_rego_no, vessel_name, vessel_length, vessel_draft
                'additional_vessels': additional_vessels,
                'mooring': self.mooring,
                'expiry_date': self.expiry_date.strftime('%d/%m/%Y'),
                'public_url': get_public_url(),
            }
            return context
        except Exception as e:
            msg = 'Mooring Site Licence: {} cannot generate licence. {}'.format(self.lodgement_number, str(e))
            logger.error(msg)
            raise e

    def save(self, *args, **kwargs):
        if self.lodgement_number == '':
            self.lodgement_number = self.prefix + '{0:06d}'.format(self.next_id)
        kwargs.pop('version_user', None)
        kwargs.pop('version_comment', None)
        kwargs['no_revision'] = True
        super(Approval, self).save(*args, **kwargs)

    def internal_reissue(self):
        ## now reissue approval
        self.current_proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
        self.current_proposal.save()
        self.reissued=True
        self.save()
        # Create a log entry for the proposal and approval
        self.current_proposal.log_user_action(ProposalUserAction.ACTION_REISSUE_APPROVAL.format(self.lodgement_number))
        self.approval.log_user_action(ApprovalUserAction.ACTION_REISSUE_APPROVAL.format(self.lodgement_number))
        ## final approval
        self.current_proposal.final_approval()

    def update_auth_user_permits(self):
        if hasattr(self, 'mooring'):
            moa_set = MooringOnApproval.objects.filter(
                mooring=self.mooring,
                approval__status__in=[Approval.APPROVAL_STATUS_SUSPENDED, Approval.APPROVAL_STATUS_CURRENT,],
            )
            for moa in moa_set:
                if type(moa.approval.child_obj) == AuthorisedUserPermit:
                    moa.approval.child_obj.update_moorings(self)

    def _create_new_sticker_by_proposal(self, proposal):
        new_sticker = Sticker.objects.create(
            approval=self,
            vessel_ownership=proposal.vessel_ownership,
            fee_constructor=proposal.fee_constructor,
            proposal_initiated=proposal,
            fee_season=self.latest_applied_season,
        )
        logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')
        return new_sticker

    def manage_stickers(self, proposal):
        logger.info(f'Managing stickers for the MooringSiteLicence: [{self}]...')

        if proposal.approval and proposal.approval.reissued:
            # Can only change the conditions, so goes to Approved
            proposal.processing_status = Proposal.PROCESSING_STATUS_APPROVED
            proposal.save()
            proposal.save(f'Processing status: [{Proposal.PROCESSING_STATUS_APPROVED}] has been set to the proposal: [{proposal}]')
            return [], []

        if proposal.proposal_type.code == PROPOSAL_TYPE_NEW:
            # New sticker created with status Ready
            new_sticker = self._create_new_sticker_by_proposal(proposal)
            logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}]')

            # Application goes to status Printing Sticker
            proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            proposal.save(f'Processing status: [{Proposal.PROCESSING_STATUS_PRINTING_STICKER}] has been set to the proposal: [{proposal}]')
            logger.info(f'')
            return [], []

        elif proposal.proposal_type.code == PROPOSAL_TYPE_SWAP_MOORINGS:
            stickers_to_be_kept = []  # Store all the stickers we want to keep
            new_sticker_created = False
            new_sticker_status = Sticker.STICKER_STATUS_READY  # Default to 'ready'

            for vessel_ownership in self.vessel_ownership_list:
                new_sticker = Sticker.objects.create(
                    approval=self,
                    # vessel_ownership=proposal.vessel_ownership,
                    vessel_ownership=vessel_ownership,
                    fee_constructor=proposal.fee_constructor,
                    proposal_initiated=proposal,
                    fee_season=self.latest_applied_season,
                    status=new_sticker_status,
                )
                new_sticker_created = True
                stickers_to_be_kept.append(new_sticker)
                logger.info(f'New Sticker: [{new_sticker}] has been created for the vessel_ownership: [{vessel_ownership}] of the licence: [{self}].')

            stickers_current = self.stickers.filter(
                status__in=[
                    Sticker.STICKER_STATUS_CURRENT,
                    Sticker.STICKER_STATUS_AWAITING_PRINTING,
                ]
            )
            # CurrentStickers - StickersToBeKept = StickersToBeReturned
            stickers_to_be_returned = [sticker for sticker in stickers_current if sticker not in stickers_to_be_kept]

            # Update sticker status
            self._update_status_of_sticker_to_be_removed(stickers_to_be_returned)

            # new_proposal_status = Proposal.PROCESSING_STATUS_APPROVED  # Default to 'approved'
            # if stickers_to_be_returned_by_vessel_sold:
            #     new_proposal_status = Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED
            # elif new_sticker_created:
            #     new_proposal_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            # proposal.processing_status = new_proposal_status
            proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            proposal.save()
            logger.info(f'Status: [{Proposal.PROCESSING_STATUS_PRINTING_STICKER}] has been set to the proposal: [{proposal}]')

            return [], stickers_to_be_returned

        elif proposal.proposal_type.code == PROPOSAL_TYPE_AMENDMENT:
            # Amendment (vessel(s) may be changed)
            stickers_to_be_kept = []  # Store all the stickers we want to keep
            new_sticker_created = False
            new_sticker_status = Sticker.STICKER_STATUS_READY  # Default to 'ready'

            # Check if there are stickers to be returned due to the vessel sale
            stickers_to_be_returned_by_vessel_sold = self.stickers.filter(
                status__in=[
                    Sticker.STICKER_STATUS_TO_BE_RETURNED,
                ],
                vessel_ownership__end_date__isnull=False,  # This line means the vessel was sold before.
            )
            if stickers_to_be_returned_by_vessel_sold:
                new_sticker_status = Sticker.STICKER_STATUS_NOT_READY_YET

            for vessel_ownership in self.vessel_ownership_list:
                # Loop through all the current(active) vessel_ownerships of this mooring site licence

                # Check if the proposal makes changes on the existing vessel and which requires the sticker colour to be changed
                sticker_colour_to_be_changed = False
                # The proposal currently being processed can make changes only on the latest vessel added to this mooring licence.
                if proposal.previous_application:  # This should be True because this proposal is an amendment proposal
                    if proposal.vessel_ownership == vessel_ownership == proposal.previous_application.vessel_ownership:
                        # This vessel_ownership is shared by both proposal currently being processed and the one before.
                        # Which means the vessel is not changed.  However, there is still the case where the existing sticker
                        # should be replaced by a new one due to the sticker colour changes.
                        logger.info(f'VesselOwnership: [{vessel_ownership}] is shared by both the proposal: [{proposal}] and the previous proposal: [{proposal.previous_application}].  Which means the vessel is not changed.')
                        next_colour = Sticker.get_vessel_size_colour_by_length(proposal.vessel_length)
                        current_colour = Sticker.get_vessel_size_colour_by_length(proposal.previous_application.vessel_length)
                        if next_colour != current_colour:
                            logger.info(f'Sticker colour: [{next_colour}] for the proposal: [{proposal}] is different from the sticker colour: [{current_colour}].')
                            # Same vessel but sticker colour must be changed
                            sticker_colour_to_be_changed = True

                # Look for the sticker for the vessel
                stickers_for_this_vessel = self.stickers.filter(
                    status__in=[
                        Sticker.STICKER_STATUS_CURRENT,
                        Sticker.STICKER_STATUS_AWAITING_PRINTING,
                    ],
                    vessel_ownership=vessel_ownership,
                )
                if not stickers_for_this_vessel or sticker_colour_to_be_changed:
                    # Sticker for this vessel not found OR new sticker colour is different from the existing sticker colour
                    # A new sticker should be created
                    new_sticker = Sticker.objects.create(
                        approval=self,
                        vessel_ownership=proposal.vessel_ownership,
                        fee_constructor=proposal.fee_constructor,
                        proposal_initiated=proposal,
                        fee_season=self.latest_applied_season,
                        status=new_sticker_status,
                    )
                    new_sticker_created = True
                    stickers_to_be_kept.append(new_sticker)
                    logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')
                else:
                    sticker = stickers_for_this_vessel.order_by('number').last()
                    stickers_to_be_kept.append(sticker)

            # Calculate the stickers which are no longer needed.
            stickers_current = self.stickers.filter(
                status__in=[
                    Sticker.STICKER_STATUS_CURRENT,
                    Sticker.STICKER_STATUS_AWAITING_PRINTING,
                ]
            )
            # CurrentStickers - StickersToBeKept = StickersToBeReturned
            stickers_to_be_returned = [sticker for sticker in stickers_current if sticker not in stickers_to_be_kept]

            # Update sticker status
            self._update_status_of_sticker_to_be_removed(stickers_to_be_returned)

            new_proposal_status = Proposal.PROCESSING_STATUS_APPROVED  # Default to 'approved'
            if stickers_to_be_returned_by_vessel_sold:
                new_proposal_status = Proposal.PROCESSING_STATUS_STICKER_TO_BE_RETURNED
            elif new_sticker_created:
                new_proposal_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
            proposal.processing_status = new_proposal_status
            proposal.save()
            logger.info(f'Status: [{new_proposal_status}] has been set to the proposal: [{proposal}]')

            return [], stickers_to_be_returned

        elif proposal.proposal_type.code == PROPOSAL_TYPE_RENEWAL:
            # Renewal (vessel changed, null vessel)
            stickers_to_be_kept = []
            stickers_to_be_replaced = []

            for vessel_ownership in self.vessel_ownership_list:
                # Look for the sticker for the vessel
                existing_sticker = self.stickers.filter(
                    status__in=(
                        Sticker.STICKER_STATUS_CURRENT,
                        Sticker.STICKER_STATUS_AWAITING_PRINTING,
                        Sticker.STICKER_STATUS_TO_BE_RETURNED,),
                    vessel_ownership=vessel_ownership,
                )
                if existing_sticker:
                    existing_sticker = existing_sticker.first()
                    stickers_to_be_replaced.append(existing_sticker)

                # Sticker not found --> Create it
                new_sticker = Sticker.objects.create(
                    approval=self,
                    # vessel_ownership=proposal.vessel_ownership,
                    vessel_ownership=vessel_ownership,
                    fee_constructor=proposal.fee_constructor,
                    proposal_initiated=proposal,
                    fee_season=self.latest_applied_season,
                )
                stickers_to_be_kept.append(new_sticker)
                logger.info(f'New Sticker: [{new_sticker}] has been created for the proposal: [{proposal}].')

                if existing_sticker:
                    new_sticker.sticker_to_replace = existing_sticker
                    new_sticker.save()

            if len(stickers_to_be_kept):
                proposal.processing_status = Proposal.PROCESSING_STATUS_PRINTING_STICKER
                proposal.save()

            return [], []  # Is this correct?

    def get_current_vessel_ownership_on_approvals(self):
        vooas = self.vesselownershiponapproval_set.filter(
            Q(end_date__isnull=True) &
            Q(vessel_ownership__end_date__isnull=True)
        )
        return vooas

    def get_current_min_vessel_applicable_length(self):
            min_length = 0
            vessel_details = self.current_vessel_attributes('vessel_details')
            for vessel_detail in vessel_details:
                if not min_length or vessel_detail.vessel_applicable_length < min_length:
                    min_length = vessel_detail.vessel_applicable_length
            return min_length

    def current_vessel_attributes(self, attribute=None, proposal=None):
        attribute_list = []
        vooas = self.get_current_vessel_ownership_on_approvals()
        for vooa in vooas:
            if not attribute:
                attribute_list.append(vooa.vessel_ownership)
            elif attribute == 'vessel_details':
                attribute_list.append(vooa.vessel_ownership.vessel.latest_vessel_details)
            elif attribute == 'vessel':
                attribute_list.append(vooa.vessel_ownership.vessel)
            elif attribute == 'current_vessels_for_licence_doc':
                attribute_list.append({
                    "rego_no": vooa.vessel_ownership.vessel.rego_no,
                    "latest_vessel_details": vooa.vessel_ownership.vessel.latest_vessel_details
                })
        return attribute_list

    @property
    def vessel_list(self):
        return self.current_vessel_attributes('vessel')

    def get_most_recent_end_date(self):
        # This function returns None if no vessels have been sold
        # If even one vessel has been sold, this function returns the end_date even if there is a current vessel.
        proposal = self.proposal_set.latest('vessel_ownership__end_date')
        return proposal.vessel_ownership.end_date

    @property
    def vessel_list_for_payment(self):
        vessels = []
        for proposal in self.proposal_set.all():
            if (
                    proposal.final_status and
                    proposal.vessel_details and
                    not proposal.vessel_ownership.end_date  # and  # vessel has not been sold by this owner
                    # We don't worry about if existing vessel(s) is removed or not because regardless of it, payments made for that vessel.
                    # not proposal.vessel_ownership.mooring_licence_end_date  # vessel has been unchecked
            ):
                if proposal.vessel_details.vessel not in vessels:
                    vessels.append(proposal.vessel_details.vessel)
        return vessels

    # This function may not be used
    @property
    def vessel_details_list_for_payment(self):
        vessel_details = []
        for proposal in self.proposal_set.all():
            if (
                    proposal.final_status and
                    proposal.vessel_details not in vessel_details and
                    not proposal.vessel_ownership.end_date  # vessel has not been sold by this owner
                    # We don't worry about if existing vessel(s) is removed or not because regardless of it, payments made for that vessel.
            ):
                vessel_details.append(proposal.vessel_details)
        return vessel_details

    @property
    def vessel_details_list(self):
        return self.current_vessel_attributes('vessel_details')

    @property
    def vessel_ownership_list(self):
        return self.current_vessel_attributes()

    #@property
    #def current_vessels(self):
    #    return self.current_vessel_attributes('current_vessels')

    def get_current_vessels_for_licence_doc(self):
        return self.current_vessel_attributes('current_vessels_for_licence_doc')

    ##@property
    #def current_vessels_rego(self, proposal=None):
    #    return self.current_vessel_attributes('current_vessels_rego', proposal)


class PreviewTempApproval(Approval):
    class Meta:
        app_label = 'mooringlicensing'


class ApprovalLogEntry(CommunicationsLogEntry):
    approval = models.ForeignKey(Approval, related_name='comms_logs', on_delete=models.CASCADE)

    class Meta:
        app_label = 'mooringlicensing'

    def save(self, **kwargs):
        # save the application reference if the reference not provided
        if not self.reference:
            self.reference = self.approval.id
        super(ApprovalLogEntry, self).save(**kwargs)

class ApprovalLogDocument(Document):
# def update_approval_comms_log_filename(instance, filename):
#     return '{}/proposals/{}/approvals/communications/{}'.format(settings.MEDIA_APP_DIR, instance.log_entry.approval.current_proposal.id,filename)
    @staticmethod
    def relative_path_to_file(proposal_id, filename):
        return f'proposal/{proposal_id}/approvals/communications/{filename}'

    def upload_to(self, filename):
        proposal_id = self.approval.current_proposal.id
        return self.relative_path_to_file(proposal_id, filename)

    log_entry = models.ForeignKey('ApprovalLogEntry', related_name='documents', null=True, on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_approval_comms_log_filename, null=True, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )

    class Meta:
        app_label = 'mooringlicensing'

class ApprovalUserAction(UserAction):
    ACTION_CREATE_APPROVAL = "Create approval {}"
    ACTION_UPDATE_APPROVAL = "Create approval {}"
    ACTION_EXPIRE_APPROVAL = "Expire approval {}"
    ACTION_CANCEL_APPROVAL = "Cancel approval {}"
    ACTION_EXTEND_APPROVAL = "Extend approval {}"
    ACTION_SUSPEND_APPROVAL = "Suspend approval {}"
    ACTION_REINSTATE_APPROVAL = "Reinstate approval {}"
    ACTION_SURRENDER_APPROVAL = "Surrender approval {}"
    ACTION_RENEW_APPROVAL = "Create renewal Application for approval {}"
    ACTION_AMEND_APPROVAL = "Create amendment Application for approval {}"
    ACTION_SWAP_MOORINGS = "Create swap moorings Application for approval {}"
    ACTION_REISSUE_APPROVAL = "Reissue approval {}"
    ACTION_REISSUE_APPROVAL_ML = "Reissued due to change in Mooring Site Licence {}"
    ACTION_RENEWAL_NOTICE_SENT_FOR_APPROVAL = "Renewal notice sent for approval: {}"

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ('-when',)

    @classmethod
    def log_action(cls, approval, action, user=None):
        return cls.objects.create(
            approval=approval,
            who=user.id if user else None,
            what=str(action)
        )

    # who = models.ForeignKey(EmailUser, null=True, blank=True, on_delete=models.CASCADE)
    who = models.IntegerField(null=True, blank=True)
    when = models.DateTimeField(null=False, blank=False, auto_now_add=True)
    what = models.TextField(blank=False)
    approval= models.ForeignKey(Approval, related_name='action_logs', on_delete=models.CASCADE)


class DcvOrganisation(RevisionedMixin):
    name = models.CharField(max_length=128, null=True, blank=True)
    abn = models.CharField(max_length=50, null=True, blank=True, verbose_name='ABN', unique=True)

    def __str__(self):
        return self.name + f'(id: {self.id})'

    class Meta:
        app_label = 'mooringlicensing'


class DcvVessel(RevisionedMixin):
    rego_no = models.CharField(max_length=200, unique=True, blank=True, null=True)
    vessel_name = models.CharField(max_length=400, blank=True)
    # dcv_organisation = models.ForeignKey(DcvOrganisation, blank=True, null=True, on_delete=models.SET_NULL)
    dcv_organisations = models.ManyToManyField(DcvOrganisation, related_name='dcv_vessels')

    def __str__(self):
        return self.rego_no + f'(id: {self.id})'

    class Meta:
        app_label = 'mooringlicensing'


class DcvAdmission(RevisionedMixin):
    LODGEMENT_NUMBER_PREFIX = 'DCV'

    submitter = models.IntegerField(blank=True, null=True)
    lodgement_number = models.CharField(max_length=10, blank=True, unique=True)
    lodgement_datetime = models.DateTimeField(blank=True, null=True)  # This is the datetime when payment
    skipper = models.CharField(max_length=50, blank=True, null=True)
    contact_number = models.CharField(max_length=50, blank=True, null=True)
    dcv_vessel = models.ForeignKey(DcvVessel, blank=True, null=True, related_name='dcv_admissions', on_delete=models.SET_NULL)
    dcv_organisation = models.ForeignKey(DcvOrganisation, blank=True, null=True, related_name='dcv_admissions', on_delete=models.SET_NULL)

    @property
    def admin_group(self):
        return ledger_api_client.managed_models.SystemGroup.objects.get(name=GROUP_DCV_PERMIT_ADMIN)

    @property
    def admin_recipients(self):
        return [retrieve_email_userro(i).email for i in self.admin_group.get_system_group_member_ids()]


    class Meta:
        app_label = 'mooringlicensing'

    @property
    def submitter_obj(self):
        return retrieve_email_userro(self.submitter) if self.submitter else None

    def __str__(self):
        lodgement_number = '---'
        if self.lodgement_number:
            lodgement_number = self.lodgement_number
        return f'{lodgement_number} (id: {self.id})'

    @property
    def fee_paid(self):
        # if self.invoice and self.invoice.payment_status in ['paid', 'over_paid']:
        if self.invoice and get_invoice_payment_status(self.invoice.id) in ['paid', 'over_paid']:
            return True
        return False

    @property
    def invoice(self):
        invoice = None
        for dcv_admission_fee in self.dcv_admission_fees.all():
            if dcv_admission_fee.fee_items.count():
                invoice = Invoice.objects.get(reference=dcv_admission_fee.invoice_reference)
        return invoice

    @classmethod
    def get_next_id(cls):
        ids = map(int, [i.split(cls.LODGEMENT_NUMBER_PREFIX)[1] for i in cls.objects.all().values_list('lodgement_number', flat=True) if i])
        ids = list(ids)
        return max(ids) + 1 if len(ids) else 1

    def save(self, **kwargs):
        if self.lodgement_number in ['', None]:
            self.lodgement_number = self.LODGEMENT_NUMBER_PREFIX + '{0:06d}'.format(self.get_next_id())
        super(DcvAdmission, self).save(**kwargs)

    def generate_dcv_admission_doc(self):
        for arrival in self.dcv_admission_arrivals.all():
            # Document is created per arrival
            permit_document = create_dcv_admission_document(arrival)

    def get_summary(self):
        summary = []
        for arrival in self.dcv_admission_arrivals.all():
            summary.append(arrival.get_summary())
        return summary

    def get_admission_urls(self):
        urls = []
        for admission in self.dcv_admission_documents.all():
            urls.append(admission._file.url)
        return urls

    def create_fee_lines(self):
        logger.info(f'Creating fee lines for the DcvAdmission: [{self}]...')

        db_processes_after_success = {}

        target_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        target_date = target_datetime.date()
        target_datetime_str = target_datetime.astimezone(pytz.timezone(TIME_ZONE)).strftime('%d/%m/%Y %I:%M %p')

        db_processes_after_success['datetime_for_calculating_fee'] = target_datetime.__str__()

        application_type = ApplicationType.objects.get(code=settings.APPLICATION_TYPE_DCV_ADMISSION['code'])
        # vessel_length = 1  # any number greater than 0
        vessel_length = GlobalSettings.default_values[GlobalSettings.KEY_MINIMUM_VESSEL_LENGTH] + 1
        proposal_type = None
        oracle_code = application_type.get_oracle_code_by_date(target_date=target_date)

        line_items = []
        for dcv_admission_arrival in self.dcv_admission_arrivals.all():
            fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(application_type, dcv_admission_arrival.arrival_date)
            db_processes_after_success['fee_constructor_id'] = fee_constructor.id

            if not fee_constructor:
                raise Exception('FeeConstructor object for the ApplicationType: {} and the Season: {}'.format(application_type, dcv_admission_arrival.arrival_date))

            fee_items = []
            number_of_people_str = []
            total_amount = 0

            for number_of_people in dcv_admission_arrival.numberofpeople_set.all():
                if number_of_people.number:
                    # When more than 1 people,
                    fee_item = fee_constructor.get_fee_item(vessel_length, proposal_type, dcv_admission_arrival.arrival_date, number_of_people.age_group, number_of_people.admission_type)
                    fee_item.number_of_people = number_of_people.number
                    fee_items.append(fee_item)
                    number_of_people_str.append('[{}-{}: {}]'.format(number_of_people.age_group, number_of_people.admission_type, number_of_people.number))
                    total_amount += fee_item.get_absolute_amount() * number_of_people.number

            db_processes_after_success['fee_item_ids'] = [item.id for item in fee_items]

            private_visit = 'YES' if dcv_admission_arrival.private_visit else 'NO'

            if settings.DEBUG:
                # In debug environment, we want to avoid decimal number which may cuase some kind of error.
                total_amount = math.ceil(total_amount)
                total_amount_excl_tax = math.ceil(calculate_excl_gst(total_amount)) if fee_constructor.incur_gst else math.ceil(total_amount)
            else:
                total_amount_excl_tax = calculate_excl_gst(total_amount) if fee_constructor.incur_gst else total_amount

            line_item = {
                'ledger_description': '{} Fee: {} (Arrival: {}, Private: {}, {})'.format(
                    fee_constructor.application_type.description,
                    self.lodgement_number,
                    dcv_admission_arrival.arrival_date,
                    private_visit,
                    ', '.join(number_of_people_str),
                ),
                'oracle_code': oracle_code,
                'price_incl_tax': total_amount,
                'price_excl_tax': total_amount_excl_tax,
                'quantity': 1,
            }
            line_items.append(line_item)

        logger.info('{}'.format(line_items))

        return line_items, db_processes_after_success

class DcvAdmissionArrival(RevisionedMixin):
    dcv_admission = models.ForeignKey(DcvAdmission, null=True, blank=True, related_name='dcv_admission_arrivals', on_delete=models.SET_NULL)
    arrival_date = models.DateField(null=True, blank=True)
    departure_date = models.DateField(null=True, blank=True)
    private_visit = models.BooleanField(default=False)
    fee_season = models.ForeignKey('FeeSeason', null=True, blank=True, on_delete=models.SET_NULL)
    start_date = models.DateField(null=True, blank=True)  # This is the season.start_date when payment
    end_date = models.DateField(null=True, blank=True)  # This is the season.end_date when payment
    fee_constructor = models.ForeignKey('FeeConstructor', on_delete=models.CASCADE, blank=True, null=True, related_name='dcv_admission_arrivals')

    class Meta:
        app_label = 'mooringlicensing'

    def get_context_for_licence_permit(self):
        today = timezone.localtime(timezone.now()).date()
        context = {
            'lodgement_number': self.dcv_admission.lodgement_number,
            'organisation_name': self.dcv_admission.dcv_organisation.name,
            'organisation_abn': self.dcv_admission.dcv_organisation.abn if self.dcv_admission.dcv_organisation.abn else '',
            'issue_date': today.strftime('%d/%m/%Y'),
            'vessel_rego_no': self.dcv_admission.dcv_vessel.rego_no,
            'vessel_name': self.dcv_admission.dcv_vessel.vessel_name,
            'arrival': self.get_summary(),
            'public_url': get_public_url(),
            'date_registered': self.dcv_admission.lodgement_datetime.strftime('%d/%m/%Y'),
        }
        return context

    def __str__(self):
        return '{} ({}-{})'.format(self.dcv_admission, self.arrival_date, self.departure_date)

    def get_summary(self):
        summary_dict = {'arrival_date': self.arrival_date.strftime('%d/%m/%Y') if self.arrival_date else '', 'departure_date': self.departure_date.strftime('%d/%m/%Y') if self.departure_date else ''}
        for age_group_choice in AgeGroup.NAME_CHOICES:
            age_group = AgeGroup.objects.get(code=age_group_choice[0])
            dict_type = {}
            for admission_type_choice in AdmissionType.TYPE_CHOICES:
                admission_type = AdmissionType.objects.get(code=admission_type_choice[0])
                num_of_people = self.numberofpeople_set.get(age_group=age_group, admission_type=admission_type)
                dict_type[admission_type_choice[0]] = num_of_people.number
            summary_dict[age_group_choice[0]] = dict_type
        return summary_dict


class AgeGroup(models.Model):
    AGE_GROUP_ADULT = 'adult'
    AGE_GROUP_CHILD = 'child'

    NAME_CHOICES = (
        (AGE_GROUP_ADULT, 'Adult'),
        (AGE_GROUP_CHILD, 'Child'),
    )
    code = models.CharField(max_length=40, choices=NAME_CHOICES, default=NAME_CHOICES[0][0])

    def __str__(self):
        for item in self.NAME_CHOICES:
            if self.code == item[0]:
                return item[1]
        return ''

    class Meta:
        app_label = 'mooringlicensing'


class AdmissionType(models.Model):
    ADMISSION_TYPE_LANDING = 'landing'
    ADMISSION_TYPE_EXTENDED_STAY = 'extended_stay'
    ADMISSION_TYPE_WATER_BASED = 'water_based'  # Replace 'not_landing'
    ADMISSION_TYPE_APPROVED_EVENTS = 'approved_events'

    TYPE_CHOICES = (
        (ADMISSION_TYPE_LANDING, 'Landing'),
        (ADMISSION_TYPE_EXTENDED_STAY, 'Extended stay'),
        (ADMISSION_TYPE_WATER_BASED, 'Water based'),
        (ADMISSION_TYPE_APPROVED_EVENTS, 'Approved events'),
    )
    code = models.CharField(max_length=40, choices=TYPE_CHOICES, default=TYPE_CHOICES[0][0])

    def __str__(self):
        for item in self.TYPE_CHOICES:
            if self.code == item[0]:
                return item[1]
        return ''

    class Meta:
        app_label = 'mooringlicensing'


class NumberOfPeople(RevisionedMixin):
    number = models.PositiveSmallIntegerField(null=True, blank=True, default=0)
    dcv_admission_arrival = models.ForeignKey(DcvAdmissionArrival, null=True, blank=True, on_delete=models.SET_NULL)
    age_group = models.ForeignKey(AgeGroup, null=True, blank=True, on_delete=models.SET_NULL)
    admission_type = models.ForeignKey(AdmissionType, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        return '{} ({}, {}, {})'.format(self.number, self.dcv_admission_arrival, self.age_group, self.admission_type)


class DcvPermit(RevisionedMixin):
    description = 'DCV Permit'

    DCV_PERMIT_STATUS_CURRENT = 'current'
    DCV_PERMIT_STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = (
        (DCV_PERMIT_STATUS_CURRENT, 'Current'),
        (DCV_PERMIT_STATUS_EXPIRED, 'Expired'),
    )
    LODGEMENT_NUMBER_PREFIX = 'DCVP'

    submitter = models.IntegerField(blank=True, null=True)
    fee_season = models.ForeignKey('FeeSeason', null=True, blank=True, related_name='dcv_permits', on_delete=models.SET_NULL)
    dcv_vessel = models.ForeignKey(DcvVessel, blank=True, null=True, related_name='dcv_permits', on_delete=models.SET_NULL)
    dcv_organisation = models.ForeignKey(DcvOrganisation, blank=True, null=True, on_delete=models.SET_NULL)
    renewal_sent = models.BooleanField(default=False)
    migrated = models.BooleanField(default=False)

    # Following fields are null unless payment success
    lodgement_number = models.CharField(max_length=10, blank=True,)  # lodgement_number is assigned only when payment success, which means if this is None, the permit has not been issued.
    lodgement_datetime = models.DateTimeField(blank=True, null=True)  # This is the datetime assigned on the success of payment
    start_date = models.DateField(null=True, blank=True)  # This is the season.start_date assigned on the success of payment
    end_date = models.DateField(null=True, blank=True)  # This is the season.end_date assigned on the success of payment

    @property
    def submitter_obj(self):
        return retrieve_email_userro(self.submitter) if self.submitter else None

    def create_fee_lines(self):
        """ Create the ledger lines - line item for application fee sent to payment system """
        logger.info(f'Creating fee lines for the DcvPermit: [{self}]...')

        # Any changes to the DB should be made after the success of payment process
        db_processes_after_success = {}

        application_type = ApplicationType.objects.get(code=settings.APPLICATION_TYPE_DCV_PERMIT['code'])
        # vessel_length = 1  # any number greater than 0
        vessel_length = GlobalSettings.default_values[GlobalSettings.KEY_MINIMUM_VESSEL_LENGTH] + 1
        proposal_type = None

        target_datetime = datetime.datetime.now(pytz.timezone(TIME_ZONE))
        target_date = target_datetime.date()
        target_datetime_str = target_datetime.astimezone(pytz.timezone(TIME_ZONE)).strftime('%d/%m/%Y %I:%M %p')

        fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_season(
            application_type, self.fee_season
        )
        if not fee_constructor:
            # Fees have not been configured for this application type and date
            logger.error(f'FeeConstructor object for the ApplicationType: {application_type} and the Season: {self.fee_season} has not been configured yet.')
            raise Exception(f'No fees are configured for the season: {self.fee_season} for the ApplicationType: {application_type}.')

        if target_date > fee_constructor.end_date:
            logger.error(f'Somehow, fee_constructor retrieved for fee calculation is ended on {fee_constructor.end_date}, which is before the target_date: {target_date}')
            raise Exception(f'Something wrong with fee configurations...')

        if target_date < fee_constructor.start_date:
            # Customer is applying for the future permit.
            target_date = fee_constructor.start_date


        fee_item = fee_constructor.get_fee_item(vessel_length, proposal_type, target_date)

        db_processes_after_success['fee_item_id'] = fee_item.id
        db_processes_after_success['fee_constructor_id'] = fee_constructor.id
        db_processes_after_success['season_start_date'] = fee_constructor.fee_season.start_date.__str__()
        db_processes_after_success['season_end_date'] = fee_constructor.fee_season.end_date.__str__()
        db_processes_after_success['datetime_for_calculating_fee'] = target_datetime.__str__()

        if settings.DEBUG:
            # In debug environment, we want to avoid decimal number which may cuase some kind of error.
            total_amount = math.ceil(fee_item.amount)
            total_amount_excl_tax = math.ceil(ledger_api_client.utils.calculate_excl_gst(fee_item.amount)) if fee_constructor.incur_gst else math.ceil(fee_item.amount)
        else:
            total_amount = fee_item.amount
            total_amount_excl_tax = ledger_api_client.utils.calculate_excl_gst(fee_item.amount) if fee_constructor.incur_gst else fee_item.amount

        line_items = [
            {
                # 'ledger_description': '{} Fee: {} (Season: {} to {}) @{}'.format(
                'ledger_description': '{} Fee: {} @{}'.format(
                    fee_constructor.application_type.description,
                    self.lodgement_number,
                    # fee_constructor.fee_season.start_date.strftime('%d/%m/%Y'),
                    # fee_constructor.fee_season.end_date.strftime('%d/%m/%Y'),
                    target_datetime_str,
                ),
                # 'oracle_code': application_type.oracle_code,
                'oracle_code': ApplicationType.get_current_oracle_code_by_application(application_type.code),
                'price_incl_tax': total_amount,
                'price_excl_tax': total_amount_excl_tax,
                'quantity': 1,
            },
        ]

        logger.info('{}'.format(line_items))

        return line_items, db_processes_after_success

    @property
    def postal_address_line1(self):
        ret_value = ''
        if self.submitter:
            if self.submitter_obj.postal_same_as_residential:
                ret_value = self.submitter_obj.residential_address.line1
            else:
                if self.submitter_obj.postal_address:
                    ret_value = self.submitter_obj.postal_address.line1
            if not ret_value:
                # Shouldn't reach here, but if so, just return residential address
                ret_value = self.submitter_obj.residential_address.line1
        return ret_value

    @property
    def postal_address_line2(self):
        ret_value = ''
        if self.submitter:
            if self.submitter_obj.postal_same_as_residential:
                ret_value = self.submitter_obj.residential_address.line2
            else:
                if self.submitter_obj.postal_address:
                    ret_value = self.submitter_obj.postal_address.line2
            if not ret_value:
                # Shouldn't reach here, but if so, just return residential address
                ret_value = self.submitter_obj.residential_address.line2
        return ret_value

    @property
    def postal_address_state(self):
        ret_value = ''
        if self.submitter:
            if self.submitter_obj.postal_same_as_residential:
                ret_value = self.submitter_obj.residential_address.state
            else:
                if self.submitter_obj.postal_address:
                    ret_value = self.submitter_obj.postal_address.state
            if not ret_value:
                # Shouldn't reach here, but if so, just return residential address
                ret_value = self.submitter_obj.residential_address.state
        return ret_value

    @property
    def postal_address_suburb(self):
        ret_value = ''
        if self.submitter:
            if self.submitter_obj.postal_same_as_residential:
                ret_value = self.submitter_obj.residential_address.locality
            else:
                if self.submitter_obj.postal_address:
                    ret_value = self.submitter_obj.postal_address.locality
            if not ret_value:
                # Shouldn't reach here, but if so, just return residential address
                ret_value = self.submitter_obj.residential_address.locality
        return ret_value

    @property
    def postal_address_postcode(self):
        ret_value = ''
        if self.submitter:
            if self.submitter_obj.postal_same_as_residential:
                ret_value = self.submitter_obj.residential_address.postcode
            else:
                if self.submitter_obj.postal_address:
                    ret_value = self.submitter_obj.postal_address.postcode
            if not ret_value:
                # Shouldn't reach here, but if so, just return residential address
                ret_value = self.submitter_obj.residential_address.postcode
        return ret_value

    def get_context_for_licence_permit(self):
        context = {
            'lodgement_number': self.lodgement_number,
            'organisation_name': self.dcv_organisation.name if self.dcv_organisation else '',
            'organisation_abn': self.dcv_organisation.abn if self.dcv_organisation else '',
            'issue_date': self.lodgement_datetime.strftime('%d/%m/%Y'),
            'p_address_line1': self.postal_address_line1,
            'p_address_line2': self.postal_address_line2,
            'p_address_suburb': self.postal_address_suburb,
            'p_address_state': self.postal_address_state,
            'p_address_postcode': self.postal_address_postcode,
            'vessel_rego_no': self.dcv_vessel.rego_no,
            'vessel_name': self.dcv_vessel.vessel_name,
            'expiry_date': self.end_date.strftime('%d/%m/%Y'),
            'public_url': get_public_url(),
            'submitter_fullname': self.submitter.get_full_name(),
        }
        return context

    def get_licence_document_as_attachment(self):
        attachment = None
        if self.dcv_permit_documents.count():
            licence_document = self.dcv_permit_documents.first()._file
            if licence_document is not None:
                file_name = self.licence_document.name
                attachment = (file_name, licence_document.file.read(), 'application/pdf')
        return attachment

    def get_target_date(self, applied_date):
        return applied_date

    @property
    def expiry_date(self):
        return self.expiry_date

    @property
    def fee_paid(self):
        # if self.invoice and self.invoice.payment_status in ['paid', 'over_paid']:
        if self.invoice and get_invoice_payment_status(self.invoice.id) in ['paid', 'over_paid']:
            return True
        return False

    @property
    def invoice(self):
        invoice = None
        for dcv_permit_fee in self.dcv_permit_fees.all():
            if dcv_permit_fee.fee_items.count():
                try:
                    invoice = Invoice.objects.get(reference=dcv_permit_fee.invoice_reference)
                except Invoice.DoesNotExist:
                    logger.error(f'Invoice: [{dcv_permit_fee.invoice_reference}] not found.')
        return invoice

        # if self.dcv_permit_fees.count() < 1:
        #     return None
        # elif self.dcv_permit_fees.count() == 1:
        #     dcv_permit_fee = self.dcv_permit_fees.first()
        #     try:
        #         invoice = Invoice.objects.get(reference=dcv_permit_fee.invoice_reference)
        #         return invoice
        #     except:
        #         return None
        # else:
        #     msg = 'DcvPermit: {} has {} DcvPermitFees.  There should be 0 or 1.'.format(self, self.dcv_permit_fees.count())
        #     logger.error(msg)
        #     raise ValidationError(msg)

    @classmethod
    def get_next_id(cls):
        ids = map(int, [i.split(cls.LODGEMENT_NUMBER_PREFIX)[1] for i in cls.objects.all().values_list('lodgement_number', flat=True) if i])
        ids = list(ids)
        return max(ids) + 1 if len(ids) else 1

    @property
    def status(self, target_date=datetime.datetime.now(pytz.timezone(TIME_ZONE)).date()):
        if self.start_date:
            if self.start_date <= target_date <= self.end_date:
                return self.STATUS_CHOICES[0]
            else:
                return self.STATUS_CHOICES[1]
        else:
            return None

    def save(self, **kwargs):
        logger.info(f"Saving DcvPermit: {self}...")
        if self.lodgement_number in ['', None] and self.lodgement_datetime:  # start_date is null unless payment success
            # Only when the fee has been paid, a lodgement number is assigned
            logger.info(f'DcvPermit: [{self}] has no lodgement number.')
            self.lodgement_number = self.LODGEMENT_NUMBER_PREFIX + '{0:06d}'.format(self.get_next_id())

        super(DcvPermit, self).save(**kwargs)
        logger.info(f"DcvPermit: [{self}] has been updated with the lodgement_number: [{self.lodgement_number}].")

    def generate_dcv_permit_doc(self):
        permit_document = create_dcv_permit_document(self)

    def get_fee_amount_adjusted(self, fee_item, vessel_length):
        # Adjust fee amount if needed
        # return fee_item.amount
        return fee_item.get_absolute_amount(vessel_length)

    class Meta:
        app_label = 'mooringlicensing'

    def __str__(self):
        # return f'{self.lodgement_number} (M)' if self.migrated else f'{self.lodgement_number}'
        lodgement_number = '---'
        if self.lodgement_number:
            lodgement_number = self.lodgement_number
        lodgement_number = f'{lodgement_number} (M)' if self.migrated else lodgement_number
        return f'{lodgement_number} (id: {self.id})'

def update_dcv_admission_doc_filename(instance, filename):
    return '{}/dcv_admissions/{}/admissions/{}'.format(settings.MEDIA_APP_DIR, instance.id, filename)


def update_dcv_permit_doc_filename(instance, filename):
    return '{}/dcv_permits/{}/permits/{}'.format(settings.MEDIA_APP_DIR, instance.id, filename)


class DcvAdmissionDocument(Document):
    @staticmethod
    def relative_path_to_file(dcv_admission_id, filename):
        return f'dcv_admission/{dcv_admission_id}/dcv_admission_documents/{filename}'

    def upload_to(self, filename):
        dcv_admission_id = self.dcv_admission.id
        return self.relative_path_to_file(dcv_admission_id, filename)

    dcv_admission = models.ForeignKey(DcvAdmission, related_name='dcv_admission_documents', on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_dcv_admission_doc_filename, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    can_delete = models.BooleanField(default=False)  # after initial submit prevent document from being deleted

    def delete(self, using=None, keep_parents=False):
        if self.can_delete:
            return super(DcvAdmissionDocument, self).delete(using, keep_parents)
        logger.info('Cannot delete existing document object after Application has been submitted : {}'.format(self.name))

    class Meta:
        app_label = 'mooringlicensing'


class DcvPermitDocument(Document):
    @staticmethod
    def relative_path_to_file(dcv_permit_id, filename):
        return f'dcv_permit/{dcv_permit_id}/dcv_permit_documents/{filename}'

    def upload_to(self, filename):
        dcv_permit_id = self.dcv_permit.id
        return self.relative_path_to_file(dcv_permit_id, filename)

    dcv_permit = models.ForeignKey(DcvPermit, related_name='dcv_permit_documents', on_delete=models.CASCADE)
    # _file = models.FileField(upload_to=update_dcv_permit_doc_filename, max_length=512)
    _file = models.FileField(
        null=True,
        max_length=512,
        storage=private_storage,
        upload_to=upload_to
    )
    can_delete = models.BooleanField(default=False)  # after initial submit prevent document from being deleted

    def delete(self, using=None, keep_parents=False):
        if self.can_delete:
            return super(DcvPermitDocument, self).delete(using, keep_parents)
        logger.info('Cannot delete existing document object after Application has been submitted : {}'.format(self.name))

    class Meta:
        app_label = 'mooringlicensing'


class Sticker(models.Model):
    STICKER_STATUS_NOT_READY_YET = 'not_ready_yet'  # This status is assigned to the new replacement sticker.  Once the old sticker is returned, the status changes to STICKER_STATUS_READY.
    STICKER_STATUS_READY = 'ready'  # This status is assigned to the new sticker.  Cron job picks up the sticker with this status and process it.
    STICKER_STATUS_AWAITING_PRINTING = 'awaiting_printing'
    STICKER_STATUS_CURRENT = 'current'
    STICKER_STATUS_TO_BE_RETURNED = 'to_be_returned'
    STICKER_STATUS_RETURNED = 'returned'
    STICKER_STATUS_LOST = 'lost'
    STICKER_STATUS_EXPIRED = 'expired'
    STICKER_STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STICKER_STATUS_READY, 'Ready'),
        (STICKER_STATUS_NOT_READY_YET, 'Not Ready Yet'),
        (STICKER_STATUS_AWAITING_PRINTING, 'Awaiting Printing'),
        (STICKER_STATUS_CURRENT, 'Current'),
        (STICKER_STATUS_TO_BE_RETURNED, 'To be Returned'),
        (STICKER_STATUS_RETURNED, 'Returned'),
        (STICKER_STATUS_LOST, 'Lost'),
        (STICKER_STATUS_EXPIRED, 'Expired'),
        (STICKER_STATUS_CANCELLED, 'Cancelled')
    )
    EXPOSED_STATUS = (
        STICKER_STATUS_AWAITING_PRINTING,
        STICKER_STATUS_CURRENT,
        STICKER_STATUS_TO_BE_RETURNED,
        STICKER_STATUS_RETURNED,
        STICKER_STATUS_LOST,
        STICKER_STATUS_EXPIRED,
        STICKER_STATUS_CANCELLED,
    )
    STATUSES_FOR_FILTER = (
        STICKER_STATUS_AWAITING_PRINTING,
        STICKER_STATUS_CURRENT,
        STICKER_STATUS_TO_BE_RETURNED,
        STICKER_STATUS_RETURNED,
        STICKER_STATUS_LOST,
        STICKER_STATUS_EXPIRED,
        STICKER_STATUS_CANCELLED,
    )
    STATUSES_AS_CURRENT = [
        STICKER_STATUS_NOT_READY_YET,
        STICKER_STATUS_READY,
        STICKER_STATUS_AWAITING_PRINTING,
        STICKER_STATUS_CURRENT,
    ]

    colour_default = 'green'
    colour_matrix = [
        {'length': 10, 'colour': 'Green'},
        {'length': 12, 'colour': 'Grey'},
        {'length': 14, 'colour': 'Purple'},
        {'length': 16, 'colour': 'Blue'},
        {'length': 1000, 'colour': 'White'},  # This is returned whenever any of the previous doesn't fit the requirement.
    ]
    number = models.CharField(max_length=9, blank=True, default='')
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_CHOICES[0][0])
    sticker_printing_batch = models.ForeignKey(StickerPrintingBatch, blank=True, null=True, on_delete=models.SET_NULL)  # When None, most probably 'awaiting_
    sticker_printing_response = models.ForeignKey(StickerPrintingResponse, blank=True, null=True, on_delete=models.SET_NULL)
    approval = models.ForeignKey(Approval, blank=True, null=True, related_name='stickers', on_delete=models.SET_NULL)  # Sticker links to either approval or dcv_permit, never to both.
    dcv_permit = models.ForeignKey(DcvPermit, blank=True, null=True, related_name='stickers', on_delete=models.SET_NULL)
    printing_date = models.DateField(blank=True, null=True)  # The day this sticker printed
    mailing_date = models.DateField(blank=True, null=True)  # The day this sticker sent
    fee_constructor = models.ForeignKey('FeeConstructor', blank=True, null=True, on_delete=models.SET_NULL)
    fee_season = models.ForeignKey('FeeSeason', blank=True, null=True, on_delete=models.SET_NULL)
    vessel_ownership = models.ForeignKey('VesselOwnership', blank=True, null=True, on_delete=models.SET_NULL)
    proposal_initiated = models.ForeignKey('Proposal', blank=True, null=True, on_delete=models.SET_NULL)  # This propposal created this sticker object.  Can be None when sticker created by RequestNewSticker action or so.
    sticker_to_replace = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)  # This sticker object replaces the sticker_to_replace for renewal
    date_created = models.DateTimeField(blank=True, null=True, auto_now_add=True)
    date_updated = models.DateTimeField(blank=True, null=True, auto_now=True)

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ['-date_updated', '-date_created', '-number',]

    def get_invoices(self):
        invoices = []
        for action_detail in self.sticker_action_details.all():
            try:
                if action_detail.sticker_action_fee and action_detail.sticker_action_fee.invoice_reference:
                    inv = Invoice.objects.get(reference=action_detail.sticker_action_fee.invoice_reference)
                    invoices.append(inv)
            except Invoice.DoesNotExist:
                logger.error(f'Invoice: [{action_detail.sticker_action_fee.invoice_reference}]) does not exist for the Sticker: [{self}].')
            except Exception as e:
                logger.error(f'Error raised when retrieving invoice(s) for the Sticker: [{self}]')
        return invoices

    def get_moorings(self):
        moorings = []

        if self.approval:
            if self.approval.code == AuthorisedUserPermit.code:
                valid_moas = self.mooringonapproval_set.filter(Q(end_date__isnull=True))
                for moa in valid_moas:
                    moorings.append(moa.mooring)
            elif self.approval.code == MooringLicence.code:
                if hasattr(self.approval.child_obj, 'mooring'):
                    moorings.append(self.approval.child_obj.mooring)
                else:
                    logger.error(
                        'Failed to retrieve the mooring for the sticker {} because the associated MooringLicence {} does not have a mooring'.format(
                            self.number, self.approval.lodgement_number))

        return moorings

    def __str__(self):
        if self.number:
            return f'ID: {self.id} (#{self.number}, {self.status})'
        else:
            return f'ID: {self.id} (#---, {self.status})'

    def record_lost(self):
        logger.info(f'record_lost() is being accessed for the sticker: [{self}].')
        self.status = Sticker.STICKER_STATUS_LOST
        self.save()
        logger.info(f'Status: [{Sticker.STICKER_STATUS_LOST}] has been set to the sticker: [{self}].')

    def record_returned(self):
        logger.info(f'record_returned() is being accessed for the sticker: [{self}].')
        self.status = Sticker.STICKER_STATUS_RETURNED
        self.save()
        logger.info(f'Status: [{Sticker.STICKER_STATUS_RETURNED}] has been set to the sticker: [{self}].')

    def request_replacement(self, new_status):
        logger.info(f'record_replacement() is being accessed for the sticker: [{self}].')
        self.status = new_status
        self.save()
        logger.info(f'Status: [{new_status}] has been set to the sticker: [{self}].')

        # Create replacement sticker
        new_sticker = Sticker.objects.create(
            approval=self.approval,
            vessel_ownership=self.vessel_ownership,
            fee_constructor=self.fee_constructor,
            fee_season=self.approval.latest_applied_season,
        )
        logger.info(f'New Sticker: [{new_sticker}] has been created for the approval: [{self.approval}].')

        return new_sticker

    def get_sticker_colour(self):
        # colour = self.approval.child_obj.sticker_colour
        # colour += '/' + self.get_vessel_size_colour()
        colour = ''
        if type(self.approval.child_obj) not in [AnnualAdmissionPermit,]:
            colour = self.get_vessel_size_colour()
        return colour

    def get_white_info(self):
        white_info = ''
        colour = self.get_sticker_colour()
        if colour in [AuthorisedUserPermit.sticker_colour + '/white', MooringLicence.sticker_colour + '/white',]:
            if self.vessel_applicable_length > 26:
                white_info = self.vessel_applicable_length
            elif self.vessel_applicable_length > 24:
                white_info = 26
            elif self.vessel_applicable_length > 22:
                white_info = 24
            elif self.vessel_applicable_length > 20:
                white_info = 22
            elif self.vessel_applicable_length > 18:
                white_info = 20
            elif self.vessel_applicable_length > 16:
                white_info = 18
        return white_info

    @staticmethod
    def get_vessel_size_colour_by_length(vessel_length):
        return_colour = ''
        for item in Sticker.colour_matrix:
            if vessel_length <= item['length']:
                return_colour = item['colour']
                break
        if not return_colour:
            # Just in case there is no colour set, return the last colour configured in the list
            item = Sticker.colour_matrix[-1]
            return_colour = item['colour']
        return return_colour

    def get_vessel_size_colour(self, vessel_length=None):
        if not vessel_length:
            vessel_length = self.vessel_applicable_length
        return Sticker.get_vessel_size_colour_by_length(vessel_length)

    @property
    def next_number(self):
        min_dcv_sticker_number = GlobalSettings.objects.get(key=GlobalSettings.KEY_MINUMUM_STICKER_NUMBER_FOR_DCV_PERMIT).value
        min_dcv_sticker_number = int(min_dcv_sticker_number)
        try:
            ids = [int(i) for i in Sticker.objects.all().values_list('number', flat=True) if i and int(i) < min_dcv_sticker_number]
            return max(ids) + 1 if ids else 1
        except Exception as e:
            print(e)

    def save(self, *args, **kwargs):
        super(Sticker, self).save(*args, **kwargs)
        if self.status not in [Sticker.STICKER_STATUS_NOT_READY_YET, Sticker.STICKER_STATUS_READY,]:
            # We don't want to assign a number yet to not_ready_yet sticker.
            if self.number == '':
                # Should not reach here, a new number is assigned to a sticker when exporting sticker data to the sticker company.
                # Ref: export_and_email_sticker_data.py
                self.number = '{0:07d}'.format(self.next_number)
                self.save()

    @property
    def first_name(self):
        # if self.approval and self.approval.submitter:
        #     return self.approval.submitter_obj.first_name
        # return '---'
        return self.approval.postal_first_name

    @property
    def last_name(self):
        # if self.approval and self.approval.submitter:
        #     return self.approval.submitter_obj.last_name
        # return '---'
        return self.approval.postal_last_name

    @property
    def postal_address_line1(self):
        # if self.approval and self.approval.submitter and self.approval.submitter_obj.postal_address:
        #     return self.approval.submitter_obj.postal_address.line1
        # return '---'
        return self.approval.postal_address_line1

    @property
    def postal_address_line2(self):
        # if self.approval and self.approval.submitter and self.approval.submitter_obj.postal_address:
        #     return self.approval.submitter_obj.postal_address.line2
        # return '---'
        return self.approval.postal_address_line2

    @property
    def postal_address_state(self):
        # if self.approval and self.approval.submitter and self.approval.submitter_obj.postal_address:
        #     return self.approval.submitter_obj.postal_address.state
        # return '---'
        return self.approval.postal_address_state

    @property
    def postal_address_suburb(self):
        # if self.approval and self.approval.submitter and self.approval.submitter_obj.postal_address:
        #     return self.approval.submitter_obj.postal_address.locality
        # return '---'
        return self.approval.postal_address_suburb

    @property
    def postal_address_postcode(self):
        # if self.approval and self.approval.submitter and self.approval.submitter_obj.postal_address:
        #     return self.approval.submitter_obj.postal_address.postcode
        # return '---'
        return self.approval.postal_address_postcode

    @property
    def vessel_registration_number(self):
        if self.vessel_ownership and self.vessel_ownership.vessel:
            return self.vessel_ownership.vessel.rego_no
        return '---'

    @property
    def vessel_applicable_length(self):
        if self.vessel_ownership and self.vessel_ownership.vessel and self.vessel_ownership.vessel.latest_vessel_details:
            return self.vessel_ownership.vessel.latest_vessel_details.vessel_applicable_length
        raise ValueError('Vessel size not found for the sticker: {}'.format(self))


class StickerActionDetail(models.Model):
    sticker = models.ForeignKey(Sticker, blank=True, null=True, related_name='sticker_action_details', on_delete=models.SET_NULL)
    reason = models.TextField(blank=True)
    date_created = models.DateTimeField(blank=True, null=True, auto_now_add=True)
    date_updated = models.DateTimeField(blank=True, null=True, auto_now=True)
    date_of_lost_sticker = models.DateField(blank=True, null=True)
    date_of_returned_sticker = models.DateField(blank=True, null=True)
    action = models.CharField(max_length=50, null=True, blank=True)
    # user = models.ForeignKey(EmailUser, null=True, blank=True, on_delete=models.SET_NULL)
    user = models.IntegerField(null=True, blank=True)
    sticker_action_fee = models.ForeignKey(StickerActionFee, null=True, blank=True, related_name='sticker_action_details', on_delete=models.SET_NULL)
    waive_the_fee = models.BooleanField(default=False)

    class Meta:
        app_label = 'mooringlicensing'
        ordering = ['-date_created']


@receiver(pre_delete, sender=Approval)
def delete_documents(sender, instance, *args, **kwargs):
    #if hasattr(instance, 'documents'):
    #    for document in instance.documents.all():
    if hasattr(instance, 'approval_documents'):
        for document in instance.approval_documents.all():
            try:
                document.delete()
            except:
                pass


import reversion
reversion.register(WaitingListOfferDocument, follow=[])
reversion.register(RenewalDocument, follow=['renewal_document'])
reversion.register(AuthorisedUserSummaryDocument, follow=['approvals'])
reversion.register(ApprovalDocument, follow=['approvalhistory_set', 'licence_document', 'cover_letter_document'])
reversion.register(MooringOnApproval, follow=['approval', 'mooring', 'sticker'])
reversion.register(VesselOwnershipOnApproval, follow=['approval', 'vessel_ownership'])
reversion.register(ApprovalHistory, follow=[])
#reversion.register(Approval, follow=['proposal_set', 'ria_generated_proposal', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances'])
reversion.register(Approval)
reversion.register(WaitingListAllocation, follow=['proposal_set', 'ria_generated_proposal', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'approval_documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances'])
reversion.register(AnnualAdmissionPermit, follow=['proposal_set', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'approval_documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances'])
reversion.register(AuthorisedUserPermit, follow=['proposal_set', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'approval_documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances'])
reversion.register(MooringLicence, follow=['proposal_set', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'approval_documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances', 'mooring'])
reversion.register(PreviewTempApproval, follow=['proposal_set', 'waiting_list_offer_documents', 'renewal_documents', 'authorised_user_summary_documents', 'approval_documents', 'mooringonapproval_set', 'vesselownershiponapproval_set', 'approvalhistory_set', 'replace', 'comms_logs', 'action_logs', 'stickers', 'compliances'])
reversion.register(ApprovalLogEntry, follow=['documents'])
reversion.register(ApprovalLogDocument, follow=[])
reversion.register(ApprovalUserAction, follow=[])
reversion.register(DcvOrganisation, follow=['dcv_vessels', 'dcvpermit_set'])
reversion.register(DcvVessel, follow=['dcv_admissions', 'dcv_permits'])
# reversion.register(DcvAdmission, follow=['dcv_admission_arrivals', 'admissions'])
reversion.register(DcvAdmission, follow=['dcv_admission_arrivals', 'dcv_admission_documents'])
reversion.register(DcvAdmissionArrival, follow=['numberofpeople_set'])
reversion.register(AgeGroup, follow=['numberofpeople_set'])
reversion.register(AdmissionType, follow=['numberofpeople_set'])
reversion.register(NumberOfPeople, follow=[])
reversion.register(DcvPermit, follow=['dcv_permit_documents', 'stickers'])
reversion.register(DcvAdmissionDocument, follow=[])
reversion.register(DcvPermitDocument, follow=[])
reversion.register(Sticker, follow=['mooringonapproval_set', 'approvalhistory_set', 'sticker_action_details'])
reversion.register(StickerActionDetail, follow=[])
