import logging
from django.db.models import Q
# from ledger.settings_base import TIME_ZONE
from ledger_api_client.settings_base import TIME_ZONE
import pytz
from datetime import datetime
from decimal import Decimal
from math import ceil

from django.conf import settings
# from ledger.accounts.models import EmailUser,Address
# from ledger.payments.invoice.models import Invoice
from ledger_api_client.ledger_models import EmailUserRO as EmailUser, Invoice, Address

from mooringlicensing.components.main.models import ApplicationType
# from mooringlicensing.components.main.utils import retrieve_email_user
from mooringlicensing.components.payments_ml.models import FeeConstructor
from mooringlicensing.components.proposals.models import (
    Proposal,
    ProposalApplicant,
    ProposalUserAction,
    ProposalLogEntry,
    VesselLogEntry,
    MooringLogEntry,
    ProposalRequirement,
    ProposalStandardRequirement,
    ProposalDeclinedDetails,
    AmendmentRequest,
    AmendmentReason,
    RequirementDocument,
    VesselDetails,
    VesselOwnership,
    Vessel,
    MooringBay,
    ProposalType,
    Company,
    CompanyOwnership,
    Mooring, MooringLicenceApplication, AuthorisedUserApplication
)
from mooringlicensing.ledger_api_utils import retrieve_email_userro, get_invoice_payment_status
from mooringlicensing.components.approvals.models import MooringLicence, MooringOnApproval, Approval
from mooringlicensing.components.main.serializers import CommunicationLogEntrySerializer, InvoiceSerializer, \
    EmailUserSerializer
from mooringlicensing.components.users.serializers import UserSerializer, ProposalApplicantSerializer
from mooringlicensing.components.users.serializers import UserAddressSerializer
from rest_framework import serializers
from mooringlicensing.helpers import is_internal
from mooringlicensing.settings import PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL

# logger = logging.getLogger('mooringlicensing')
logger = logging.getLogger(__name__)


class EmailUserAppViewSerializer(serializers.ModelSerializer):
    residential_address = UserAddressSerializer()

    class Meta:
        model = EmailUser
        fields = ('id',
                  'email',
                  'first_name',
                  'last_name',
                  'dob',
                  'title',
                  'organisation',
                  'residential_address',
                  'email',
                  'phone_number',
                  'mobile_number',)


class ProposalTypeSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProposalType
        fields = (
            'id',
            'code',
            'description',
        )


class CompanySerializer(serializers.ModelSerializer):

    class Meta:
        model = Company
        fields = (
                'id',
                'name',
                )


class CompanyOwnershipSerializer(serializers.ModelSerializer):
    company = CompanySerializer()
    percentage = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = CompanyOwnership
        fields = (
                'id',
                'blocking_proposal',
                # 'status',
                'vessel',
                'company',
                'percentage',
                'start_date',
                'end_date',
                'created',
                'updated',
                )

    def validate_percentage(self, value):
        if not value:
            return 0
        try:
            return int(value)
        except ValueError:
            raise serializers.ValidationError('You must supply an integer value.')

class MooringSerializer(serializers.ModelSerializer):
    mooring_bay_name = serializers.SerializerMethodField()

    class Meta:
        model = Mooring
        fields = '__all__'

    def get_mooring_bay_name(self, obj):
        return obj.mooring_bay.name


class MooringSimpleSerializer(serializers.ModelSerializer):
    mooring_bay_name = serializers.CharField(source='mooring_bay.name')

    class Meta:
        model = Mooring
        fields = (
            'name',
            'mooring_bay_name',
        )


class BaseProposalSerializer(serializers.ModelSerializer):
    readonly = serializers.SerializerMethodField(read_only=True)
    documents_url = serializers.SerializerMethodField()
    # allowed_assessors = EmailUserSerializer(many=True)
    allowed_assessors = serializers.SerializerMethodField()
    # submitter = EmailUserSerializer()
    get_history = serializers.ReadOnlyField()
    application_type_code = serializers.SerializerMethodField()
    application_type_text = serializers.SerializerMethodField()
    approval_type_text = serializers.SerializerMethodField()
    application_type_dict = serializers.SerializerMethodField()
    editable_vessel_details = serializers.SerializerMethodField()
    proposal_type = ProposalTypeSerializer()
    invoices = serializers.SerializerMethodField()
    start_date = serializers.ReadOnlyField()
    # 20220308: end_date req in this serializer?
    #end_date = serializers.ReadOnlyField()
    previous_application_vessel_details_id = serializers.SerializerMethodField()
    previous_application_preferred_bay_id = serializers.SerializerMethodField()
    current_vessels_rego_list = serializers.SerializerMethodField()
    approval_lodgement_number = serializers.SerializerMethodField()
    approval_vessel_rego_no = serializers.SerializerMethodField()
    waiting_list_application_id = serializers.SerializerMethodField()
    authorised_user_moorings_str = serializers.SerializerMethodField()
    previous_application_vessel_details_obj = serializers.SerializerMethodField()
    previous_application_vessel_ownership_obj = serializers.SerializerMethodField()
    # max_vessel_length_with_no_payment = serializers.SerializerMethodField()
    approval_reissued = serializers.SerializerMethodField()
    vessel_on_proposal = serializers.SerializerMethodField()
    proposal_applicant = ProposalApplicantSerializer()
    uuid = serializers.SerializerMethodField()
    amendment_requests = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = (
                'id',
                'start_date',
                #'end_date',
                'application_type_code',
                'application_type_text',
                'approval_type_text',
                'application_type_dict',
                'proposal_type',
                'approval_level',
                'title',
                'customer_status',
                'processing_status',
                'applicant_type',
                'applicant',
                'submitter',
                'assigned_officer',
                'get_history',
                'lodgement_date',
                'modified_date',
                'documents',
                'requirements',
                'readonly',
                'can_user_edit',
                'can_user_view',
                'documents_url',
                'lodgement_number',
                'lodgement_sequence',
                'can_officer_process',
                'allowed_assessors',
                'pending_amendment_request',
                'is_amendment_proposal',
                'applicant_details',
                'fee_paid',
                'invoices',
                ## vessel fields
                'rego_no',
                'vessel_id',
                'vessel_details_id',
                'vessel_ownership_id',
                'vessel_type',
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                'vessel_beam',
                'vessel_weight',
                'berth_mooring',
                'dot_name',
                'percentage',
                'editable_vessel_details',
                'individual_owner',
                'insurance_choice',
                'preferred_bay_id',
                'silent_elector',
                'bay_preferences_numbered',
                'site_licensee_email',
                'mooring_id',
                'mooring_authorisation_preference',
                'company_ownership_name',
                'company_ownership_percentage',
                'previous_application_id',
                'previous_application_vessel_details_id',
                'previous_application_preferred_bay_id',
                'current_vessels_rego_list',
                'approval_lodgement_number',
                'approval_vessel_rego_no',
                'waiting_list_application_id',
                'authorised_user_moorings_str',
                'temporary_document_collection_id',
                'previous_application_vessel_details_obj',
                'previous_application_vessel_ownership_obj',
                # 'max_vessel_length_with_no_payment',
                'keep_existing_mooring',
                'keep_existing_vessel',
                'approval_reissued',
                'vessel_on_proposal',
                'null_vessel_on_create',
                'proposal_applicant',
                'uuid',
                'amendment_requests',
                )
        read_only_fields=('documents',)

    def get_amendment_requests(self, obj):
        data = None
        if obj.proposalrequest_set.count():
            amendment_requests = obj.proposalrequest_set.all()
            serializer = AmendmentRequestSerializer(amendment_requests, many=True)
            data = serializer.data
        return data

    def get_uuid(self, obj):
        if hasattr(obj.child_obj, 'uuid'):
            return obj.child_obj.uuid
        else:
            return ''

    def get_allowed_assessors(self, obj):
        serializer = EmailUserSerializer(obj.allowed_assessors, many=True)
        return serializer.data

    def get_vessel_on_proposal(self, obj):
        return obj.vessel_on_proposal()

    def get_approval_reissued(self, obj):
        reissue = False
        if obj.approval:
            reissue = obj.approval.reissued
        return reissue

    def get_previous_application_vessel_details_obj(self, obj):
        #if (type(obj.child_obj) in [AuthorisedUserApplication, MooringLicenceApplication] and obj.previous_application and 
        if (obj.previous_application and obj.previous_application.vessel_details):
            return VesselDetailsSerializer(obj.previous_application.vessel_details).data

    def get_previous_application_vessel_ownership_obj(self, obj):
        #if (type(obj.child_obj) in [AuthorisedUserApplication, MooringLicenceApplication] and obj.previous_application and 
         #       obj.previous_application.vessel_ownership and obj.proposal_type.code == PROPOSAL_TYPE_RENEWAL):
        if (obj.previous_application and obj.previous_application.vessel_details):
            return VesselOwnershipSerializer(obj.previous_application.vessel_ownership).data

    #def get_authorised_user_moorings_str(self, obj):
    #    moorings_str = ''
    #    if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
    #        for moa in obj.approval.mooringonapproval_set.all():
    #            moorings_str += moa.mooring.name + ','
    #        return moorings_str[0:-1] if moorings_str else ''
    def get_authorised_user_moorings_str(self, obj):
        if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
            moorings_str = ''
            moorings_str += ', '.join([mooring.name for mooring in obj.listed_moorings.all()])
            return moorings_str

    def get_waiting_list_application_id(self, obj):
        wla_id = None
        if obj.waiting_list_allocation:
            wla_id = obj.waiting_list_allocation.current_proposal.id
        return wla_id

    def get_approval_vessel_rego_no(self, obj):
        rego_no = None
        if obj.approval and type(obj.approval) is not MooringLicence:
            rego_no = (obj.approval.current_proposal.vessel_details.vessel.rego_no if
                    obj.approval and obj.approval.current_proposal and obj.approval.current_proposal.vessel_details and
                    not obj.approval.current_proposal.vessel_ownership.end_date
                    else None)
        return rego_no

    def get_approval_lodgement_number(self, obj):
        lodgement_number = None
        if obj.approval:
            lodgement_number = obj.approval.lodgement_number
        return lodgement_number

    def get_current_vessels_rego_list(self, obj):
        if obj.approval and type(obj.approval.child_obj) is MooringLicence:
            vessels_str = ''
            vessels_str += ', '.join([vo.vessel.rego_no for vo in obj.listed_vessels.filter(end_date__isnull=True)])
            return vessels_str

    def get_previous_application_preferred_bay_id(self, obj):
        preferred_bay_id = None
        if obj.previous_application and obj.previous_application.preferred_bay:
            preferred_bay_id = obj.previous_application.preferred_bay.id
        return preferred_bay_id

    def get_previous_application_vessel_details_id(self, obj):
        vessel_details_id = None
        if obj.previous_application and obj.previous_application.vessel_details:
            vessel_details_id = obj.previous_application.vessel_details.id
        return vessel_details_id

    def get_editable_vessel_details(self, obj):
        return obj.editable_vessel_details

    def get_application_type_code(self, obj):
        return obj.application_type_code

    def get_approval_type_text(self, obj):
        return obj.approval.child_obj.description if obj.approval else None

    def get_application_type_text(self, obj):
        return obj.child_obj.description

    def get_application_type_dict(self, obj):
        return {
            'code': obj.child_obj.code,
            'description': obj.child_obj.description,
        }

    def get_documents_url(self,obj):
        return '/private-media/{}/proposals/{}/documents/'.format(settings.MEDIA_APP_DIR, obj.id)

    def get_readonly(self,obj):
        return False

    def get_processing_status(self,obj):
        return obj.get_processing_status_display()

    def get_customer_status(self,obj):
        return obj.get_customer_status_display()

    def get_fee_invoice_references(self, obj):
        ret_list = []
        if obj.application_fees.count():
            for application_fee in obj.application_fees.all():
                ret_list.append(application_fee.invoice_reference)
        return ret_list

    def get_invoices(self, obj):
        ret_list = []
        invoice_references = [item.invoice_reference for item in obj.application_fees.all()]
        invoices = Invoice.objects.filter(reference__in=invoice_references)
        if not invoices:
            return ''
        else:
            serializer = InvoiceSerializer(invoices, many=True)
            return serializer.data


class ListProposalSerializer(BaseProposalSerializer):
    # submitter = EmailUserSerializer()
    submitter = serializers.SerializerMethodField(read_only=True)
    applicant = serializers.CharField(read_only=True)
    processing_status = serializers.SerializerMethodField()
    customer_status = serializers.SerializerMethodField()
    assigned_officer = serializers.SerializerMethodField()
    assigned_approver = serializers.SerializerMethodField()
    application_type_dict = serializers.SerializerMethodField()

    assessor_process = serializers.SerializerMethodField()
    mooring = MooringSerializer()
    uuid = serializers.SerializerMethodField()
    document_upload_url = serializers.SerializerMethodField()
    can_view_payment_details = serializers.SerializerMethodField()
    invoice_links = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = (
            'id',
            'migrated',
            'application_type_dict',
            'proposal_type',
            'customer_status',
            'processing_status',
            'applicant',
            'submitter',
            'assigned_officer',
            'assigned_approver',
            'lodgement_date',
            'can_user_edit',
            'can_user_view',
            'lodgement_number',
            'assessor_process',
            'invoices',
            'mooring_id',
            'mooring',
            'uuid',
            'document_upload_url',
            'can_view_payment_details',
            'invoice_links',
            'mooring_authorisation_preference',
            'declined_by_endorser',
        )
        # the serverSide functionality of datatables is such that only columns that have field 'data' defined are requested from the serializer. We
        # also require the following additional fields for some of the mRender functions
        datatables_always_serialize = (
            'id',
            'migrated',
            'proposal_type',
            'customer_status',
            'application_type_dict',
            'processing_status',
            'submitter',
            'assigned_officer',
            'assigned_approver',
            'lodgement_date',
            'can_user_edit',
            'can_user_view',
            'lodgement_number',
            'assessor_process',
            'invoices',
            'mooring_id',
            'mooring',
            'uuid',
            'document_upload_url',
            'can_view_payment_details',
            'invoice_links',
            'mooring_authorisation_preference',
            'declined_by_endorser',
        )

    def get_submitter(self, obj):
        #use a ProposalApplicant record if available
        proposal_applicant = ProposalApplicant.objects.filter(proposal=obj)
        if proposal_applicant:
            return ProposalApplicantSerializer(proposal_applicant.first()).data
        else:
            if obj.submitter:
                from mooringlicensing.ledger_api_utils import retrieve_email_userro
                email_user = retrieve_email_userro(obj.submitter)
                return EmailUserSerializer(email_user).data
            else:
                return ""

    def get_invoice_links(self, proposal):
        links = ""
        # pdf
        for invoice in proposal.invoices_display():
            # links += "<div><a href='/payments/invoice-pdf/{}.pdf' target='_blank'><i style='color:red;' class='fa fa-file-pdf-o'></i> #{}</a></div>".format(
            #     invoice.reference, invoice.reference)
            # api_key = settings.LEDGER_API_KEY
            # url = settings.LEDGER_API_URL + '/ledgergw/invoice-pdf/' + settings.LEDGER_API_KEY + '/' + invoice.reference
            # url = get_invoice_url(invoice.reference)
            url = f'/ledger-toolkit-api/invoice-pdf/{invoice.reference}/'
            links += f"<div><a href='{url}' target='_blank'><i style='color:red;' class='fa fa-file-pdf-o'></i> #{invoice.reference}</a></div>"
        if self.context.get('request') and is_internal(self.context.get('request')) and proposal.application_fees.count():
            # paid invoices url
            invoices_str=''
            for inv in proposal.invoices_display():
                payment_status = get_invoice_payment_status(inv.id)
                if payment_status == 'paid':
                    invoices_str += 'invoice_no={}&'.format(inv.reference)
            if invoices_str:
                invoices_str = invoices_str[:-1]
                links += "<div><a href='{}/ledger/payments/oracle/payments?{}' target='_blank'>Ledger Payment</a></div>".format(settings.LEDGER_UI_URL, invoices_str)
                # refund url
                # links += "<div><a href='/proposal-payment-history-refund/{}' target='_blank'>Refund Payment</a></div>".format(proposal.id)
        return links

    def get_can_view_payment_details(self, proposal):
        if 'request' in self.context:
            from mooringlicensing.components.main.utils import is_payment_officer
            return is_payment_officer(self.context['request'].user)

    def get_document_upload_url(self, proposal):
        if proposal.application_type.code == MooringLicenceApplication.code and proposal.processing_status == Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS:
            request = self.context['request']
            return proposal.child_obj.get_document_upload_url(request)
        return ''

    def get_uuid(self, obj):
        try:
            return obj.child_obj.uuid
        except:
            return ''

    def get_assigned_officer(self,obj):
        if obj.assigned_officer:
            # return obj.assigned_officer.get_full_name()
            return retrieve_email_userro(obj.assigned_officer).get_full_name() if obj.assigned_officer else ''
        return None

    def get_assigned_approver(self,obj):
        if obj.assigned_approver:
            # return obj.assigned_approver.get_full_name()
            return retrieve_email_userro(obj.assigned_approver).get_full_name() if obj.assigned_approver else ''
        return None

    def get_assessor_process(self,obj):
        # Check if currently logged in user has access to process the proposal
        request = self.context['request']
        user = request.user
        if obj.can_officer_process:
            '''if (obj.assigned_officer and obj.assigned_officer == user) or (user in obj.allowed_assessors):
                return True'''
            if obj.assigned_officer:
                if obj.assigned_officer == user.id:
                    return True
            elif user in obj.allowed_assessors:
                return True
        return False


# class ProposalSerializerForEndorser(serializers.ModelSerializer):
#     class Meta:
#         model = Proposal
#         fields = (
#             'id',
#         )

class ProposalForEndorserSerializer(BaseProposalSerializer):
    for_endorser = serializers.SerializerMethodField()
    readonly = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = (
            'id',
            'for_endorser',
            'readonly',

            'application_type_code',
            'application_type_text',
            'approval_type_text',
            'application_type_dict',
            'proposal_type',
            'approval_level',
            'title',
            'customer_status',
            'processing_status',
            'applicant_type',
            'applicant',
            'submitter',
            'assigned_officer',
            'get_history',
            'lodgement_date',
            'modified_date',
            'documents',
            'requirements',
            'readonly',
            'can_user_edit',
            'can_user_view',
            'documents_url',
            'lodgement_number',
            'lodgement_sequence',
            'can_officer_process',
            'allowed_assessors',
            'pending_amendment_request',
            'is_amendment_proposal',
            'applicant_details',
            # 'fee_paid',
            # 'invoices',
            ## vessel fields
            # 'rego_no',
            # 'vessel_id',
            # 'vessel_details_id',
            # 'vessel_ownership_id',
            'vessel_type',
            # 'vessel_name',
            'vessel_length',
            'vessel_draft',
            'vessel_beam',
            'vessel_weight',
            'berth_mooring',
            'dot_name',
            # 'percentage',
            # 'editable_vessel_details',
            # 'individual_owner',
            'insurance_choice',
            # 'preferred_bay_id',
            # 'silent_elector',
            # 'bay_preferences_numbered',
            'site_licensee_email',
            'mooring_id',
            'mooring_authorisation_preference',
            'company_ownership_name',
            'company_ownership_percentage',
            'previous_application_id',
            'previous_application_vessel_details_id',
            'previous_application_preferred_bay_id',
            'current_vessels_rego_list',
            'approval_lodgement_number',
            'approval_vessel_rego_no',
            'waiting_list_application_id',
            'authorised_user_moorings_str',
            # 'temporary_document_collection_id',
            'previous_application_vessel_details_obj',
            'previous_application_vessel_ownership_obj',
            # 'max_vessel_length_with_no_payment',
            'keep_existing_mooring',
            'keep_existing_vessel',
            'approval_reissued',
            'vessel_on_proposal',
            'null_vessel_on_create',
            'proposal_applicant',
            'uuid',
            'amendment_requests',
        )

    def get_readonly(self, obj):
        return True
        
    def get_for_endorser(self, obj):
        return True


class ProposalSerializer(BaseProposalSerializer):
    processing_status = serializers.SerializerMethodField(read_only=True)
    customer_status = serializers.SerializerMethodField(read_only=True)

    def get_readonly(self,obj):
        return obj.can_user_view


#class SaveProposalSerializer(BaseProposalSerializer):
#    preferred_bay_id = serializers.IntegerField(write_only=True, required=False)
#    mooring_id = serializers.IntegerField(write_only=True, required=False)
#
#    class Meta:
#        model = Proposal
#        fields = (
#                'id',
#                'insurance_choice',
#                'preferred_bay_id',
#                'silent_elector',
#                'bay_preferences_numbered',
#                'site_licensee_email',
#                'mooring_id',
#                'mooring_authorisation_preference',
#                )
#        read_only_fields=('id',)


class SaveWaitingListApplicationSerializer(serializers.ModelSerializer):
    preferred_bay_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = Proposal
        fields = (
                'id',
                'preferred_bay_id',
                'silent_elector',
                'temporary_document_collection_id',
                'keep_existing_vessel',
                'auto_approve',
                )
        read_only_fields=('id',)

    def validate(self, data):
        custom_errors = {}
        proposal_type_new = ProposalType.objects.get(code=PROPOSAL_TYPE_NEW)
        if self.instance.proposal_type != proposal_type_new:
            if self.instance.previous_application.preferred_bay_id != data.get('preferred_bay_id'):
                custom_errors["Preferred bay"] = "You can not change a preferred bay"
        if self.context.get("action") == 'submit':
            if not data.get("preferred_bay_id"):
                custom_errors["Mooring Details"] = "You must choose a mooring bay"
            # electoral roll validation
            if 'silent_elector' not in data.keys():
                custom_errors["Electoral Roll"] = "You must complete this section"
            elif data.get("silent_elector"):
                if not self.instance.electoral_roll_documents.all():
                    custom_errors["Silent Elector"] = "You must provide evidence of this"
            
            # When company ownership, vessel registration document is compalsory
            if not self.instance.vessel_ownership.individual_owner:
                if not self.instance.vessel_ownership.vessel_registration_documents.count():
                    custom_errors["Copy of registration papers"] = "You must provide evidence of this"

        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)
        return data


class SaveAnnualAdmissionApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = (
                'id',
                'insurance_choice',
                'temporary_document_collection_id',
                'keep_existing_vessel',
                'auto_approve',
                )
        read_only_fields=('id',)

    def validate(self, data):
        custom_errors = {}
        #ignore_insurance_check=self.context.get("ignore_insurance_check")
        proposal = Proposal.objects.get(id=self.context.get("proposal_id"))
        ignore_insurance_check = data.get("keep_existing_vessel") and proposal.proposal_type.code != 'new'
        if self.context.get("action") == 'submit':
            if ignore_insurance_check:
                pass 
            else:
                insurance_choice = data.get("insurance_choice")
                vessel_length = proposal.vessel_details.vessel_applicable_length
                if not insurance_choice:
                    custom_errors["Insurance Choice"] = "You must make an insurance selection"
                elif vessel_length > Decimal("6.4") and insurance_choice not in ['ten_million', 'over_ten']:
                    custom_errors["Insurance Choice"] = "Insurance selected is insufficient for your nominated vessel"
            #elif not data.get("insurance_choice"):
                #custom_errors["Insurance Choice"] = "You must make an insurance selection"
        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)
        return data


class SaveMooringLicenceApplicationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Proposal
        fields = (
                'id',
                'insurance_choice',
                'silent_elector',
                'customer_status',
                'processing_status',
                'temporary_document_collection_id',
                'keep_existing_vessel',
                'auto_approve',
                )
        read_only_fields=('id',)

    def validate(self, data):
        logger.info(f'Validating the proposal: [{self.instance}]...')
        custom_errors = {}
        proposal = Proposal.objects.get(id=self.context.get("proposal_id"))
        ignore_insurance_check = data.get("keep_existing_vessel") and proposal.proposal_type.code != PROPOSAL_TYPE_NEW

        if self.context.get("action") == 'submit':
            if ignore_insurance_check:
                pass
            else:
                renewal_or_amendment_application_with_vessel = proposal.proposal_type.code in [PROPOSAL_TYPE_RENEWAL, PROPOSAL_TYPE_AMENDMENT,] and proposal.vessel_details
                new_application = proposal.proposal_type.code == PROPOSAL_TYPE_NEW
                if renewal_or_amendment_application_with_vessel or new_application:
                    logger.info(f'This proposal: [{self.instance}] is a new proposal or a renewal/amendment proposal with a vessel.')
                    insurance_choice = data.get("insurance_choice")
                    vessel_length = proposal.vessel_details.vessel_applicable_length
                    if not insurance_choice:
                        custom_errors["Insurance Choice"] = "You must make an insurance selection"
                    elif vessel_length > Decimal("6.4") and insurance_choice not in ['ten_million', 'over_ten']:
                        custom_errors["Insurance Choice"] = "Insurance selected is insufficient for your nominated vessel"
                    if not self.instance.insurance_certificate_documents.all():
                        custom_errors["Insurance Certificate"] = "Please attach"
                else:
                    logger.info(f'This proposal: [{self.instance}] is a renewal/amendment proposal without a vessel.')
                    pass
            # electoral roll validation
            if 'silent_elector' not in data.keys():
                custom_errors["Electoral Roll"] = "You must complete this section"
            elif data.get("silent_elector"):
                if not self.instance.electoral_roll_documents.all():
                    custom_errors["Silent Elector"] = "You must provide evidence of this"

            # When company ownership, vessel registration document is compalsory
            if not self.instance.vessel_ownership.individual_owner:
                if not self.instance.vessel_ownership.vessel_registration_documents.count():
                    custom_errors["Copy of registration papers"] = "You must provide evidence of this"

        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)

        return data


class SaveAuthorisedUserApplicationSerializer(serializers.ModelSerializer):
    mooring_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Proposal
        fields = (
                'id',
                'insurance_choice',
                'mooring_authorisation_preference',
                'bay_preferences_numbered',
                'site_licensee_email',
                'mooring_id',
                'customer_status',
                'processing_status',
                'temporary_document_collection_id',
                'keep_existing_mooring',
                'keep_existing_vessel',
                'auto_approve',
                )
        read_only_fields=('id',)

    def validate(self, data):
        custom_errors = {}
        #ignore_insurance_check=self.context.get("ignore_insurance_check")
        proposal = Proposal.objects.get(id=self.context.get("proposal_id"))
        ignore_insurance_check = data.get("keep_existing_vessel") and proposal.proposal_type.code != PROPOSAL_TYPE_NEW

        if self.context.get("action") == 'submit':
            if ignore_insurance_check:
                pass
            else:
                insurance_choice = data.get("insurance_choice")
                vessel_length = proposal.vessel_details.vessel_applicable_length
                if not insurance_choice:
                    custom_errors["Insurance Choice"] = "You must make an insurance selection"
                elif vessel_length > Decimal("6.4") and insurance_choice not in ['ten_million', 'over_ten']:
                    custom_errors["Insurance Choice"] = "Insurance selected is insufficient for your nominated vessel"
                if not self.instance.insurance_certificate_documents.all():
                    custom_errors["Insurance Certificate"] = "Please attach"
            # if not data.get("mooring_authorisation_preference") and not data.get("keep_existing_mooring"):
            if not data.get("mooring_authorisation_preference"):
                custom_errors["Mooring Details"] = "You must complete this tab"
            # if data.get("mooring_authorisation_preference") == 'site_licensee' and not data.get('keep_existing_mooring'):
            else:
                if data.get("mooring_authorisation_preference") == 'site_licensee':
                    if proposal.proposal_type.code == PROPOSAL_TYPE_NEW or not data.get('keep_existing_mooring'):
                        site_licensee_email = data.get("site_licensee_email")
                        mooring_id = data.get("mooring_id")
                        if not site_licensee_email:
                            custom_errors["Site Licensee Email"] = "This field should not be blank"
                        if not mooring_id:
                            custom_errors["Mooring Site ID"] = "This field should not be blank"
                        # check that the site_licensee_email matches the Mooring Licence holder
                        if mooring_id and Mooring.objects.get(id=mooring_id):
                            mooring_licence = Mooring.objects.get(id=mooring_id).mooring_licence
                            if not mooring_licence or mooring_licence.submitter_obj.email.lower().strip() != site_licensee_email.lower().strip():
                                custom_errors["Site Licensee Email"] = "This site licensee email does not hold the licence for the selected mooring"
        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)
        return data


class SaveDraftProposalVesselSerializer(serializers.ModelSerializer):
    percentage = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = Proposal
        fields = (
                'rego_no',
                'vessel_id',
                'vessel_type',
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                'vessel_beam',
                'vessel_weight',
                'berth_mooring',
                'percentage',
                'individual_owner',
                'company_ownership_percentage',
                'company_ownership_name',
                'dot_name',
                'temporary_document_collection_id',
                #'keep_existing_mooring',
                #'keep_existing_vessel',
                )

    def validate_percentage(self, value):
        if not value:
            return 0
        try:
            return int(value)
        except ValueError:
            raise serializers.ValidationError('You must supply an integer value.')


class ProposalDeclinedDetailsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalDeclinedDetails
        fields = '__all__'


class InternalProposalSerializer(BaseProposalSerializer):
    applicant = serializers.CharField(read_only=True)
    processing_status = serializers.SerializerMethodField(read_only=True)
    customer_status = serializers.SerializerMethodField(read_only=True)
    # submitter = UserSerializer()
    submitter = serializers.SerializerMethodField()
    proposaldeclineddetails = ProposalDeclinedDetailsSerializer()
    assessor_mode = serializers.SerializerMethodField()
    current_assessor = serializers.SerializerMethodField()
    assessor_data = serializers.SerializerMethodField()
    allowed_assessors = EmailUserSerializer(many=True)
    approval_level_document = serializers.SerializerMethodField()
    application_type = serializers.CharField(source='application_type.name', read_only=True)
    fee_invoice_url = serializers.SerializerMethodField()
    requirements_completed=serializers.SerializerMethodField()
    previous_application_vessel_details_id = serializers.SerializerMethodField()
    previous_application_preferred_bay_id = serializers.SerializerMethodField()
    mooring_licence_vessels = serializers.SerializerMethodField()
    authorised_user_moorings = serializers.SerializerMethodField()
    reissued = serializers.SerializerMethodField()
    current_vessels_rego_list = serializers.SerializerMethodField()
    application_type_code = serializers.SerializerMethodField()
    authorised_user_moorings_str = serializers.SerializerMethodField()
    waiting_list_application_id = serializers.SerializerMethodField()
    approval_type_text = serializers.SerializerMethodField()
    approval_lodgement_number = serializers.SerializerMethodField()
    approval_vessel_rego_no = serializers.SerializerMethodField()
    vessel_on_proposal = serializers.SerializerMethodField()
    proposed_issuance_approval = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = (
                'id',
                'migrated',
                'start_date',
                # 20220308: end_date req in this serializer?
                #'end_date',
                'application_type',
                'approval_level',
                'approval_level_document',
                'approval_id',
                'title',
                'customer_status',
                'processing_status',
                'applicant',
                'org_applicant',
                'proxy_applicant',
                'submitter',
                'applicant_type',
                'assigned_officer',
                'assigned_approver',
                'get_history',
                'lodgement_date',
                'modified_date',
                'documents',
                'requirements',
                'readonly',
                'can_user_edit',
                'can_user_view',
                'documents_url',
                'assessor_mode',
                'current_assessor',
                'assessor_data',
                'comment_data',
                'allowed_assessors',
                'proposed_issuance_approval',
                # 'proposed_issuance_approval2',
                'proposed_decline_status',
                'proposaldeclineddetails',
                'permit',
                'lodgement_number',
                'lodgement_sequence',
                'can_officer_process',
                'proposal_type',
                'applicant_details',
                'fee_invoice_url',
                'fee_paid',
                'requirements_completed',
                'application_type_dict',
                'vessel_details_id',
                'vessel_ownership_id',
                'insurance_choice',
                'preferred_bay_id',
                'silent_elector',
                'bay_preferences_numbered',
                'site_licensee_email',
                'mooring_id',
                'mooring_authorisation_preference',
                'previous_application_id',
                'previous_application_vessel_details_id',
                'previous_application_preferred_bay_id',
                'mooring_licence_vessels',
                'authorised_user_moorings',
                'reissued',
                'dot_name',
                'current_vessels_rego_list',
                'application_type_code',
                'authorised_user_moorings_str',
                'keep_existing_mooring',
                'keep_existing_vessel',
                # draft status
                'rego_no',
                'vessel_id',
                'vessel_details_id',
                'vessel_ownership_id',
                'vessel_type',
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                'vessel_beam',
                'vessel_weight',
                'berth_mooring',
                'dot_name',
                'percentage',
                #'editable_vessel_details',
                'individual_owner',
                'company_ownership_name',
                'company_ownership_percentage',
                'waiting_list_application_id',
                'pending_amendment_request',
                'approval_type_text',
                'approval_lodgement_number',
                'approval_vessel_rego_no',
                'vessel_on_proposal',
                'null_vessel_on_create',
                'proposal_applicant',
                'amendment_requests',
                'uuid',
                'allocated_mooring',
                )
        read_only_fields = (
            'documents',
            'requirements',
        )

    def get_proposed_issuance_approval(self, obj):
        if obj.proposed_issuance_approval and obj.proposed_issuance_approval.get('mooring_bay_id', None):
            # Add bay_name when possible to display on the frontend
            bay = MooringBay.objects.get(id=obj.proposed_issuance_approval.get('mooring_bay_id'))
            obj.proposed_issuance_approval['bay_name'] = bay.name
        return obj.proposed_issuance_approval

    def get_submitter(self, obj):
        #use a ProposalApplicant record if available
        proposal_applicant = ProposalApplicant.objects.filter(proposal=obj)
        if proposal_applicant:
            return ProposalApplicantSerializer(proposal_applicant.first()).data
        else:
            if obj.submitter:
                from mooringlicensing.ledger_api_utils import retrieve_email_userro
                email_user = retrieve_email_userro(obj.submitter)
                return EmailUserSerializer(email_user).data
            else:
                return ""

    def get_vessel_on_proposal(self, obj):
        return obj.vessel_on_proposal()

    def get_approval_vessel_rego_no(self, obj):
        rego_no = None
        if obj.approval and type(obj.approval) is not MooringLicence:
            rego_no = (obj.approval.current_proposal.vessel_details.vessel.rego_no if
                    obj.approval and obj.approval.current_proposal and obj.approval.current_proposal.vessel_details and
                    not obj.approval.current_proposal.vessel_ownership.end_date
                    else None)
        return rego_no

    def get_approval_type_text(self, obj):
        return obj.approval.child_obj.description if obj.approval else None

    def get_approval_lodgement_number(self, obj):
        lodgement_number = None
        if obj.approval:
            lodgement_number = obj.approval.lodgement_number
        return lodgement_number

    def get_waiting_list_application_id(self, obj):
        wla_id = None
        if obj.waiting_list_allocation:
            wla_id = obj.waiting_list_allocation.current_proposal.id
        return wla_id

    def get_application_type_code(self, obj):
        return obj.application_type_code

    def get_current_vessels_rego_list(self, obj):
        if obj.approval and type(obj.approval.child_obj) is MooringLicence:
            vessels_str = ''
            vessels_str += ', '.join([vo.vessel.rego_no for vo in obj.listed_vessels.filter(end_date__isnull=True)])
            return vessels_str

   # def get_current_vessels_rego_list(self, obj):
   #     vessels = []
   #     if obj.approval and type(obj.approval.child_obj) is MooringLicence:
   #         vessels = obj.approval.child_obj.current_vessels_rego(obj)
   #     return vessels

    def get_reissued(self, obj):
        return obj.approval.reissued if obj.approval else False

    #def get_authorised_user_moorings_str(self, obj):
    #    if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
    #        return obj.approval.child_obj.previous_moorings(obj)
   # def get_authorised_user_moorings_str(self, obj):
   #     if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
   #         #return obj.approval.child_obj.previous_moorings(obj)
   #         moorings_str = [mooring.name + ',' for mooring in obj.listed_moorings.all()]
   #         if moorings_str:
   #             moorings_str = moorings_str[0:-1]
   #         return moorings_str

    def get_authorised_user_moorings_str(self, obj):
        if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
            moorings_str = ''
            moorings_str += ', '.join([mooring.name for mooring in obj.listed_moorings.all()])
            return moorings_str

    def get_authorised_user_moorings(self, obj):
        moorings = []
        if type(obj.child_obj) == AuthorisedUserApplication and obj.approval:
            for moa in obj.approval.mooringonapproval_set.all():
                # if moa.mooring.mooring_licence is not None:
                #     suitable_for_mooring = True
                #     # only do check if vessel details exist
                #     if obj.vessel_details:
                #         suitable_for_mooring = moa.mooring.suitable_vessel(obj.vessel_details)
                #     color = '#000000' if suitable_for_mooring else '#FF0000'
                #     #import ipdb; ipdb.set_trace()
                #     moorings.append({
                #         "id": moa.id,
                #         "mooring_name": '<span style="color:{}">{}</span>'.format(color, moa.mooring.name),
                #         "bay": '<span style="color:{}">{}</span>'.format(color, str(moa.mooring.mooring_bay)),
                #         "site_licensee": '<span style="color:{}">RIA Allocated</span>'.format(color) if not moa.site_licensee else
                #             '<span style="color:{}">User Requested</span>'.format(color),
                #         "status": '<span style="color:{}">Current</span>'.format(color) if not moa.end_date else
                #             '<span style="color:{}">Historical</span>'.format(color),
                #         "checked": True if suitable_for_mooring and not moa.end_date else False,
                #         "suitable_for_mooring": suitable_for_mooring,
                #         #"mooring_licence_current": moa.mooring.mooring_licence.status in ['current', 'suspended'] if moa.mooring.mooring_licence else False,
                #         "mooring_licence_current": moa.mooring.mooring_licence.status in ['current', 'suspended'],
                #         })
                suitable_for_mooring = True
                # only do check if vessel details exist
                if obj.vessel_details:
                    suitable_for_mooring = moa.mooring.suitable_vessel(obj.vessel_details)
                # color = '#000000' if suitable_for_mooring else '#FF0000'
                #import ipdb; ipdb.set_trace()

                # Retrieve checkbox status for this mooring (moa.mooring)
                checked = True
                if obj.proposed_issuance_approval and 'mooring_on_approval' in obj.proposed_issuance_approval:
                    for item in obj.proposed_issuance_approval['mooring_on_approval']:
                        if  moa.id == item['id']:
                            checked = item['checked']

                moorings.append({
                    "id": moa.id,
                    "mooring_name": moa.mooring.name,
                    "bay": str(moa.mooring.mooring_bay),
                    "site_licensee": 'RIA Allocated' if not moa.site_licensee else 'User Requested',
                    "status": 'Current' if not moa.end_date else 'Historical',
                    "checked": checked,
                    'vessel_size_limit': str(moa.mooring.vessel_size_limit),
                    'vessel_draft_limit': str(moa.mooring.vessel_draft_limit),
                    "suitable_for_mooring": suitable_for_mooring,
                    "mooring_licence_current": moa.mooring.mooring_licence.status in [Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED] if moa.mooring.mooring_licence else None,
                })
        return moorings

    def get_mooring_licence_vessels(self, obj):
        vessels = []
        vessel_details = []
        if type(obj.child_obj) == MooringLicenceApplication and obj.approval:
            for vooa in obj.approval.vesselownershiponapproval_set.filter(vessel_ownership__end_date__isnull=True):
                vessel = vooa.vessel_ownership.vessel
                vessels.append(vessel)
                status = 'Current' if not vooa.end_date else 'Historical'

                # Retrieve checkbox status for this mooring (moa.mooring)
                checked = True
                if obj.proposed_issuance_approval and 'vessel_ownership' in obj.proposed_issuance_approval:
                    for item in obj.proposed_issuance_approval['vessel_ownership']:
                        if  vooa.vessel_ownership.id == item['id']:
                            checked = item['checked']

                vessel_details.append({
                    "id": vooa.vessel_ownership.id,
                    "rego": vooa.vessel_ownership.vessel.rego_no,
                    "vessel_name": vooa.vessel_ownership.vessel.latest_vessel_details.vessel_name,
                    "status": status,
                    # "checked": True if not vooa.end_date else False,
                    'checked': checked,
                })
        return vessel_details

    def get_previous_application_vessel_details_id(self, obj):
        vessel_details_id = None
        if obj.previous_application and obj.previous_application.vessel_details:
            vessel_details_id = obj.previous_application.vessel_details.id
        return vessel_details_id

    def get_previous_application_preferred_bay_id(self, obj):
        preferred_bay_id = None
        if obj.previous_application and obj.previous_application.preferred_bay:
            preferred_bay_id = obj.previous_application.preferred_bay.id
        return preferred_bay_id

    def get_approval_level_document(self,obj):
        if obj.approval_level_document is not None:
            return [obj.approval_level_document.name,obj.approval_level_document._file.url]
        else:
            return obj.approval_level_document

    def get_assessor_mode(self,obj):
        # TODO check if the proposal has been accepted or declined
        request = self.context['request']
        user = request.user._wrapped if hasattr(request.user,'_wrapped') else request.user
        return {
            'assessor_mode': True,
            'has_assessor_mode': obj.has_assessor_mode(user),
            'assessor_can_assess': obj.can_assess(user),
            'assessor_level': 'assessor',
        }

    def get_readonly(self,obj):
        return True

    def get_requirements_completed(self,obj):
        return True  # What is this for?

    def get_current_assessor(self,obj):
        return {
            'id': self.context['request'].user.id,
            'name': self.context['request'].user.get_full_name(),
            'email': self.context['request'].user.email
        }

    def get_assessor_data(self,obj):
        return obj.assessor_data

    def get_fee_invoice_url(self,obj):
        # url = '/payments/invoice-pdf/{}'.format(obj.invoice.reference) if obj.fee_paid else None
        # url = get_invoice_url(obj.invoice.reference) if obj.invoice else ''
        url = f'/ledger-toolkit-api/invoice-pdf/{obj.invoice.reference}/' if obj.invoice else ''
        return url


class ProposalUserActionSerializer(serializers.ModelSerializer):
    who = serializers.SerializerMethodField()

    class Meta:
        model = ProposalUserAction
        fields = '__all__'

    def get_who(self, obj):
        ret_name = 'System'
        if obj.who:
            name = obj.who_obj.get_full_name()
            name = name.strip()
            if name:
                ret_name = name
        return ret_name


class ProposalLogEntrySerializer(CommunicationLogEntrySerializer):
    documents = serializers.SerializerMethodField()
    class Meta:
        model = ProposalLogEntry
        fields = '__all__'
        read_only_fields = (
            'customer',
        )

    def get_documents(self,obj):
        return [[d.name,d._file.url] for d in obj.documents.all()]


class VesselLogEntrySerializer(CommunicationLogEntrySerializer):
    documents = serializers.SerializerMethodField()
    class Meta:
        model = VesselLogEntry
        fields = '__all__'
        read_only_fields = (
            'customer',
        )

    def get_documents(self,obj):
        return [[d.name,d._file.url] for d in obj.documents.all()]


class MooringLogEntrySerializer(CommunicationLogEntrySerializer):
    documents = serializers.SerializerMethodField()
    class Meta:
        model = MooringLogEntry
        fields = '__all__'
        read_only_fields = (
            'customer',
        )

    def get_documents(self,obj):
        return [[d.name,d._file.url] for d in obj.documents.all()]


class RequirementDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequirementDocument
        fields = ('id', 'name', '_file')


class ProposalRequirementSerializer(serializers.ModelSerializer):
    due_date = serializers.DateField(input_formats=['%d/%m/%Y'],required=False,allow_null=True)
    requirement_documents = RequirementDocumentSerializer(many=True, read_only=True)
    read_due_date = serializers.SerializerMethodField()

    class Meta:
        model = ProposalRequirement
        fields = (
            'id',
            'due_date',
            'free_requirement',
            'standard_requirement',
            'standard',
            'order',
            'proposal',
            'recurrence',
            'recurrence_schedule',
            'recurrence_pattern',
            'requirement',
            'is_deleted',
            'copied_from',
            'requirement_documents',
            'require_due_date',
            'copied_for_renewal',
            'read_due_date',
        )
        read_only_fields = ('id', 'order', 'requirement', 'copied_from')

    def create(self, validated_data):
        return super(ProposalRequirementSerializer, self).create(validated_data)

    def get_read_due_date(self, obj):
        due_date_str = ''
        if obj.due_date:
            due_date_str = obj.due_date.strftime('%d/%m/%Y')
        return due_date_str


class ProposalStandardRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalStandardRequirement
        fields = ('id','code','text')


class ProposedApprovalSerializer(serializers.Serializer):
    expiry_date = serializers.DateField(input_formats=['%d/%m/%Y'], required=False)
    start_date = serializers.DateField(input_formats=['%d/%m/%Y'], required=False)
    details = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cc_email = serializers.CharField(required=False, allow_null=True)
    mooring_id = serializers.IntegerField(required=False, allow_null=True)
    mooring_bay_id = serializers.IntegerField(required=False, allow_null=True)
    ria_mooring_name = serializers.CharField(required=False, allow_blank=True)
    mooring_on_approval = serializers.ListField(
            child=serializers.JSONField(),
            required=False,
            )
    vessel_ownership = serializers.ListField(
            child=serializers.JSONField(),
            required=False,
            )


class ProposedDeclineSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False)
    cc_email = serializers.CharField(required=False, allow_null=True)


class OnHoldSerializer(serializers.Serializer):
    comment = serializers.CharField()


class AmendmentRequestSerializer(serializers.ModelSerializer):

    class Meta:
        model = AmendmentRequest
        fields = '__all__'


# class BackToAssessorSerializer(serializers.ModelSerializer):
#
#     class Meta:
#         model = BackToAssessor
#         fields = '__all__'


class AmendmentRequestDisplaySerializer(serializers.ModelSerializer):
    reason = serializers.SerializerMethodField()

    class Meta:
        model = AmendmentRequest
        fields = '__all__'

    def get_reason (self,obj):
        return obj.reason.reason if obj.reason else None


class SearchKeywordSerializer(serializers.Serializer):
    number = serializers.CharField()
    id = serializers.IntegerField()
    type = serializers.CharField()
    applicant = serializers.CharField()
    text = serializers.JSONField(required=False)

class SearchReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()


class VesselSerializer(serializers.ModelSerializer):

    class Meta:
        model = Vessel
        fields = '__all__'


class VesselFullSerializer(serializers.ModelSerializer):
    vessel_details = serializers.SerializerMethodField()

    class Meta:
        model = Vessel
        fields = '__all__'

    def get_vessel_details(self, obj):
        return VesselDetailsSerializer(obj.latest_vessel_details).data


class ListVesselDetailsSerializer(serializers.ModelSerializer):
    rego_no = serializers.SerializerMethodField()
    vessel_length = serializers.SerializerMethodField()
    vessel_type = serializers.SerializerMethodField()

    class Meta:
        model = VesselDetails
        fields = (
                'id',
                'vessel_id',
                'vessel_type',
                'rego_no', # link to rego number
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                )

    def get_rego_no(self, obj):
        if obj.vessel:
            return obj.vessel.rego_no

    def get_vessel_length(self, obj):
        return obj.vessel_applicable_length

    def get_vessel_type(self, obj):
        return obj.get_vessel_type_display()


class ListVesselOwnershipSerializer(serializers.ModelSerializer):
    vessel_details = serializers.SerializerMethodField()
    emailuser = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    percentage = serializers.SerializerMethodField()
    record_sale_link = serializers.SerializerMethodField()
    sale_date = serializers.SerializerMethodField()
    class Meta:
        model = VesselOwnership
        fields = (
                'id',
                'emailuser',
                'owner_name',
                'percentage',
                'vessel_details',
                'record_sale_link',
                'sale_date',
                )

    def get_emailuser(self, obj):
        serializer = EmailUserSerializer(obj.owner.emailuser_obj)
        return serializer.data

    def get_vessel_details(self, obj):
        serializer = ListVesselDetailsSerializer(obj.vessel.latest_vessel_details)
        return serializer.data

    def get_owner_name(self, obj):
        return obj.applicable_owner_name

    def get_percentage(self, obj):
        return obj.applicable_percentage

    def get_record_sale_link(self, obj):
        return '<a href=# data-id="{}">Record Sale</a><br/>'.format(obj.id, obj.id)

    def get_sale_date(self, obj):
        sale_date = ''
        if obj.end_date:
            sale_date = obj.end_date.strftime('%d/%m/%Y')
        return sale_date

class VesselDetailsSerializer(serializers.ModelSerializer):
    read_only = serializers.SerializerMethodField()
    vessel_type_display = serializers.SerializerMethodField()

    class Meta:
        model = VesselDetails
        fields = (
                'id',
                'vessel_type',
                'vessel',
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                'vessel_beam',
                'vessel_weight',
                'berth_mooring',
                'created',
                'updated',
                'exported',
                'read_only',
                'vessel_type_display',
                )

    def get_vessel_type_display(self, obj):
        return obj.get_vessel_type_display()

    def get_read_only(self, obj):
        return False


class SaveVesselDetailsSerializer(serializers.ModelSerializer):

    class Meta:
        model = VesselDetails
        fields = (
                'vessel_type',
                'vessel', # link to rego number
                'vessel_name',
                'vessel_length',
                'vessel_draft',
                'vessel_weight',
                'berth_mooring',
                )

    def validate(self, data):
        custom_errors = {}
        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)
        return data


class VesselOwnershipSerializer(serializers.ModelSerializer):
    # company_ownership = CompanyOwnershipSerializer()
    company_ownership = serializers.SerializerMethodField()

    class Meta:
        model = VesselOwnership
        fields = '__all__'

    def get_company_ownership(self, obj):
        co = obj.get_latest_company_ownership()
        data = None
        if co:
            serializer = CompanyOwnershipSerializer(co)
            data = serializer.data
        return data


class VesselFullOwnershipSerializer(serializers.ModelSerializer):
    # company_ownership = CompanyOwnershipSerializer()
    company_ownership = serializers.SerializerMethodField()
    owner_full_name = serializers.SerializerMethodField()
    applicable_percentage = serializers.SerializerMethodField()
    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    owner_phone_number = serializers.SerializerMethodField()
    individual_owner = serializers.SerializerMethodField()
    action_link = serializers.SerializerMethodField()

    class Meta:
        model = VesselOwnership
        fields = (
                'id',
                'company_ownership',
                'percentage',
                'start_date',
                'end_date',
                'owner_full_name',
                'applicable_percentage',
                'owner_phone_number',
                'individual_owner',
                'dot_name',
                'action_link',
                )
    
    def get_company_ownership(self, obj):
        co = obj.get_latest_company_ownership()
        if co:
            serializer = CompanyOwnershipSerializer(co)
            return serializer.data
        return {}

    def get_action_link(self, obj):
        # return '/internal/person/{}'.format(obj.owner.emailuser.id)
        return '/internal/person/{}'.format(obj.owner.emailuser)

    def get_owner_full_name(self, obj):
        # return obj.owner.emailuser.get_full_name()
        owner = obj.owner.emailuser_obj
        return owner.get_full_name()

    def get_applicable_percentage(self, obj):
        return obj.applicable_percentage
        # if obj.company_ownership:
        #     return obj.company_ownership.percentage
        # else:
        #     return obj.percentage

    def get_start_date(self, obj):
        start_date_str = ''
        if obj.start_date:
            start_date_str = obj.start_date.strftime('%d/%m/%Y')
        return start_date_str

    def get_end_date(self, obj):
        end_date_str = ''
        if obj.end_date:
            end_date_str = obj.end_date.strftime('%d/%m/%Y')
        return end_date_str

    def get_owner_phone_number(self, obj):
        # return obj.owner.emailuser.phone_number if obj.owner.emailuser.phone_number else obj.owner.emailuser.mobile_number
        owner = obj.owner.emailuser_obj
        return owner.phone_number if owner.phone_number else owner.mobile_number

    def get_individual_owner(self, obj):
        return obj.individual_owner
        # individual_owner = True
        # if obj.company_ownership:
        #     individual_owner = False
        # return individual_owner


class SaveVesselOwnershipSaleDateSerializer(serializers.ModelSerializer):
    end_date = serializers.DateTimeField(input_formats=['%d/%m/%Y'],required=True,allow_null=False)

    class Meta:
        model = VesselOwnership
        fields = (
                'end_date',
                )


class VesselOwnershipSaleDateSerializer(serializers.ModelSerializer):
    end_date = serializers.SerializerMethodField()

    class Meta:
        model = VesselOwnership
        fields = (
                'end_date',
                )

    def get_end_date(self, obj):
        if obj.end_date:
            return obj.end_date.strftime('%d/%m/%Y')



class SaveVesselOwnershipSerializer(serializers.ModelSerializer):
    percentage = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = VesselOwnership
        fields = (
                'owner',
                'vessel',
                'percentage',
                # 'company_ownership',
                'start_date',
                'end_date',
                'dot_name',
                'company_ownerships',
                )

    def validate(self, data):
        logger.info(f'Validating data: [{data}]...')
        custom_errors = {}
        percentage = data.get("percentage")
        owner = data.get("owner")
        vessel = data.get("vessel")
        total = 0
        
        # Append existing company_ownerships, otherwise they are removed when save()
        # for co in self.instance.company_ownerships.all():
            # data['company_ownerships'].append(co.id)

        if data.get("percentage") and data.get("percentage") < 25:
            custom_errors["Ownership Percentage"] = "Minimum of 25 percent"
        if custom_errors.keys():
            raise serializers.ValidationError(custom_errors)
        return data

    def validate_percentage(self, value):
        logger.info(f'Validating percentage: [{value}]...')
        if not value:
            return 0
        try:
            return int(value)
        except ValueError:
            raise serializers.ValidationError('You must supply an integer value.')


class MooringBaySerializer(serializers.ModelSerializer):

    class Meta:
        model = MooringBay
        fields = '__all__'


class ListMooringSerializer(serializers.ModelSerializer):
    mooring_bay_name = serializers.SerializerMethodField()
    authorised_user_permits = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    holder = serializers.SerializerMethodField()

    class Meta:
        model = Mooring
        fields = (
                'id',
                'name',
                'mooring_bay_name',
                'vessel_size_limit',
                'vessel_draft_limit',
                'vessel_beam_limit',
                'vessel_weight_limit',
                'authorised_user_permits',
                'status',
                'holder',
            )
        datatables_always_serialize = (
                'id',
                'name',
                'mooring_bay_name',
                'vessel_size_limit',
                'vessel_draft_limit',
                'vessel_beam_limit',
                'vessel_weight_limit',
                'authorised_user_permits',
                'status',
                'holder',
            )
    
    def get_holder(self, obj):
        if obj.mooring_licence:
            try:
                return obj.mooring_licence.approval.current_proposal.proposal_applicant.get_full_name()
            except:
                return obj.mooring_licence.submitter_obj.get_full_name()
        return 'N/A'

    def get_mooring_bay_name(self, obj):
        return obj.mooring_bay.name

    def get_authorised_user_permits(self, obj):
        target_date=datetime.now(pytz.timezone(TIME_ZONE)).date()

        query = Q()
        query &= Q(mooring=obj)
        query &= Q(approval__status__in=[Approval.APPROVAL_STATUS_CURRENT, Approval.APPROVAL_STATUS_SUSPENDED,])
        query &= (Q(end_date__gt=target_date) | Q(end_date__isnull=True))

        preference_count_ria = MooringOnApproval.objects.filter(
            query,
            site_licensee=False,
        ).count()

        preference_count_site_licensee = MooringOnApproval.objects.filter(
            query,
            site_licensee=True,
        ).count()

        return {
            'ria': preference_count_ria,
            'site_licensee': preference_count_site_licensee
        }

    def get_status(status, obj):
        return obj.status


class SaveCompanyOwnershipSerializer(serializers.ModelSerializer):
    percentage = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = CompanyOwnership
        fields = (
                'company',
                'vessel',
                'percentage',
                )

    def validate_percentage(self, value):
        if not value:
            return 0
        try:
            return int(value)
        except ValueError:
            raise serializers.ValidationError('You must supply an integer value.')

