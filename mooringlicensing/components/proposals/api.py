import json
from django.db.models.functions import Concat
from django.core.paginator import Paginator, EmptyPage
import os
import traceback
import pathlib
import uuid
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, FileSystemStorage
import pytz
from django.db.models import Q, CharField, Value
from django.db import transaction
from django.core.exceptions import ValidationError
from rest_framework import viewsets, serializers, status, views
# from rest_framework.decorators import detail_route, list_route, renderer_classes
from rest_framework.decorators import renderer_classes
from rest_framework.decorators import action as detail_route
from rest_framework.decorators import action as list_route
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from datetime import datetime
# from ledger.settings_base import TIME_ZONE
from ledger_api_client.settings_base import TIME_ZONE, LOGGING
# from ledger.accounts.models import EmailUser, Address
from ledger_api_client.ledger_models import EmailUserRO as EmailUser, Address
from ledger_api_client import api
from mooringlicensing import settings
from mooringlicensing.components.main.models import GlobalSettings
from mooringlicensing.components.organisations.models import Organisation
from mooringlicensing.components.proposals.utils import (
    construct_dict_from_docs, save_proponent_data, update_proposal_applicant, make_ownership_ready,
)
from mooringlicensing.components.proposals.models import ElectoralRollDocument, HullIdentificationNumberDocument, InsuranceCertificateDocument, MooringReportDocument, VesselOwnershipCompanyOwnership, searchKeyWords, search_reference, ProposalUserAction, \
    ProposalType, ProposalApplicant, VesselRegistrationDocument
from mooringlicensing.components.main.utils import (
    get_bookings, calculate_max_length,
)

from django.core.cache import cache
from django.urls import reverse
from django.shortcuts import redirect
from mooringlicensing.components.proposals.models import (
    Proposal,
    ProposalRequirement,
    ProposalStandardRequirement,
    AmendmentRequest,
    AmendmentReason,
    VesselDetails,
    RequirementDocument,
    WaitingListApplication,
    AnnualAdmissionApplication,
    AuthorisedUserApplication,
    MooringLicenceApplication,
    VESSEL_TYPES,
    INSURANCE_CHOICES,
    Vessel,
    VesselOwnership,
    MooringBay,
    Owner,
    Company,
    CompanyOwnership,
    Mooring,
)
from mooringlicensing.components.proposals.serializers import (
    ProposalForEndorserSerializer,
    ProposalSerializer,
    InternalProposalSerializer,
    #SaveProposalSerializer,
    ProposalUserActionSerializer,
    ProposalLogEntrySerializer,
    VesselLogEntrySerializer,
    MooringLogEntrySerializer,
    ProposalRequirementSerializer,
    ProposalStandardRequirementSerializer,
    ProposedApprovalSerializer,
    AmendmentRequestSerializer,
    ListProposalSerializer,
    ListVesselDetailsSerializer,
    ListVesselOwnershipSerializer,
    VesselSerializer,
    VesselDetailsSerializer,
    VesselOwnershipSerializer,
    MooringBaySerializer, EmailUserSerializer, ProposedDeclineSerializer,
    CompanyOwnershipSerializer,
    CompanySerializer,
    SaveVesselOwnershipSaleDateSerializer,
    VesselOwnershipSaleDateSerializer,
    MooringSerializer,
    VesselFullSerializer,
    VesselFullOwnershipSerializer,
    ListMooringSerializer, SearchKeywordSerializer, SearchReferenceSerializer
)
from mooringlicensing.components.approvals.models import Approval, DcvVessel, WaitingListAllocation, Sticker, \
    DcvOrganisation, AnnualAdmissionPermit, AuthorisedUserPermit, MooringLicence, VesselOwnershipOnApproval, \
    MooringOnApproval
from mooringlicensing.components.approvals.email import send_reissue_ml_after_sale_recorded_email, send_reissue_wla_after_sale_recorded_email, \
    send_reissue_aap_after_sale_recorded_email, send_reissue_aup_after_sale_recorded_email
from mooringlicensing.components.approvals.serializers import (
        ApprovalSerializer, 
        LookupApprovalSerializer,
        )
from mooringlicensing.components.main.process_document import (
    process_generic_document, delete_document, cancel_document,
)
from mooringlicensing.components.main.decorators import (
        basic_exception_handler, 
        timeit, 
        query_debugger
        )
from mooringlicensing.components.users.serializers import ProposalApplicantSerializer
from mooringlicensing.helpers import is_authorised_to_modify, is_customer, is_internal, is_applicant_address_set
from rest_framework_datatables.pagination import DatatablesPageNumberPagination
from rest_framework_datatables.filters import DatatablesFilterBackend
from rest_framework_datatables.renderers import DatatablesRenderer
from reversion.models import Version
from copy import deepcopy

import logging
from mooringlicensing.ledger_api_utils import MyUserForLedgerAPI, update_account_details_in_ledger

from mooringlicensing.settings import PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL, \
    PAYMENT_SYSTEM_ID, BASE_DIR, MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE

logger = logging.getLogger(__name__)

class GetDcvOrganisations(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        data = DcvOrganisation.objects.all()
        data_transform = [{'id': org.id, 'name': org.name} for org in data]
        return Response(data_transform)


class GetDcvVesselRegoNos(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        search_term = request.GET.get('term', '')
        if search_term:
            data = DcvVessel.objects.filter(rego_no__icontains=search_term).values('id', 'rego_no', 'dcv_permits')[:10]
            data_transform = [{'id': rego['id'], 'text': rego['rego_no'], 'dcv_permits': rego['dcv_permits']} for rego in data]
            return Response({"results": data_transform})
        return Response()


class GetVessel(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        search_term = request.GET.get('search_term', '')
        page_number = request.GET.get('page_number', 1)
        items_per_page = 10

        if search_term:
            data_transform = []
            ### VesselDetails
            ml_data = VesselDetails.filtered_objects.filter(
                Q(vessel__rego_no__icontains=search_term) | 
                Q(vessel_name__icontains=search_term)
            ).values(
                'vessel__id', 
                'vessel__rego_no',
                'vessel_name'
            )
            paginator = Paginator(ml_data, items_per_page)
            try:
                current_page = paginator.page(page_number)
                my_objects = current_page.object_list
            except EmptyPage:
                logger.debug(f'VesselDetails empty')
                my_objects = []

            for vd in my_objects:
                data_transform.append({
                    'id': vd.get('vessel__id'), 
                    'rego_no': vd.get('vessel__rego_no'),
                    'text': vd.get('vessel__rego_no') + ' - ' + vd.get('vessel_name'),
                    'entity_type': 'ml',
                })

            ### DcvVessel
            dcv_data = DcvVessel.objects.filter(
                Q(rego_no__icontains=search_term) | 
                Q(vessel_name__icontains=search_term)
            ).values(
                'id', 
                'rego_no',
                'vessel_name'
            )
            paginator2 = Paginator(dcv_data, items_per_page)
            try:
                current_page2 = paginator2.page(page_number)
                my_objects2 = current_page2.object_list
            except EmptyPage:
                logger.debug(f'DcvVessel empty')
                my_objects2 = []

            for dcv in my_objects2:
                data_transform.append({
                    'id': dcv.get('id'), 
                    'rego_no': dcv.get('rego_no'),
                    'text': dcv.get('rego_no') + ' - ' + dcv.get('vessel_name'),
                    'entity_type': 'dcv',
                })

            ## order results
            data_transform.sort(key=lambda item: item.get("id"))

            return Response({
                "results": data_transform,
                "pagination": {
                    "more": current_page.has_next() or current_page2.has_next()
                }
            })
        return Response()


class GetMooring(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        search_term = request.GET.get('search_term', '')
        page_number = request.GET.get('page_number', 1)
        items_per_page = 10
        private_moorings = request.GET.get('private_moorings')

        if search_term:
            if private_moorings:
                data = Mooring.private_moorings.filter(name__icontains=search_term).values('id', 'name')
            else:
                data = Mooring.objects.filter(name__icontains=search_term).values('id', 'name')
            paginator = Paginator(data, items_per_page)
            try:
                current_page = paginator.page(page_number)
                my_objects = current_page.object_list
            except EmptyPage:
                my_objects = []
            
            data_transform = [{'id': mooring['id'], 'text': mooring['name']} for mooring in my_objects]

            return Response({
                "results": data_transform,
                "pagination": {
                    "more": current_page.has_next()
                }
            })
        return Response()


class GetMooringPerBay(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        from mooringlicensing.components.approvals.models import AuthorisedUserPermit, WaitingListAllocation
        mooring_bay_id = request.GET.get('mooring_bay_id')
        available_moorings = request.GET.get('available_moorings')
        vessel_details_id = request.GET.get('vessel_details_id')
        wla_id = request.GET.get('wla_id')
        aup_id = request.GET.get('aup_id')
        search_term = request.GET.get('term', '')
        num_of_moorings_to_return = int(GlobalSettings.objects.get(key=GlobalSettings.KEY_NUMBER_OF_MOORINGS_TO_RETURN_FOR_LOOKUP).value)

        if search_term:
            if available_moorings:
                if mooring_bay_id:
                    # WLA offer
                    if wla_id:
                        try:
                            wla = WaitingListAllocation.objects.get(id=int(wla_id))
                        except:
                            logger.error("wla_id {} is not an integer".format(wla_id))
                            raise serializers.ValidationError("wla_id is not an integer")
                        vessel_details_id = wla.current_proposal.vessel_details.id
                        ## restrict search results to suitable vessels
                        vessel_details = VesselDetails.objects.get(id=vessel_details_id)
                        mooring_filter = Q(
                            Q(name__icontains=search_term) &
                            Q(mooring_bay__id=mooring_bay_id) &
                            Q(vessel_size_limit__gte=vessel_details.vessel_applicable_length) &
                            Q(vessel_draft_limit__gte=vessel_details.vessel_draft)
                        )
                        data = Mooring.available_moorings.filter(mooring_filter, active=True).values('id', 'name', 'mooring_licence')[:num_of_moorings_to_return]
                    else:
                        data = Mooring.available_moorings.filter(name__icontains=search_term, mooring_bay__id=mooring_bay_id, active=True).values('id', 'name', 'mooring_licence')[:num_of_moorings_to_return]
                else:
                    data = Mooring.available_moorings.filter(name__icontains=search_term, active=True).values('id', 'name', 'mooring_licence')[:num_of_moorings_to_return]
            else:
                # aup
                if mooring_bay_id:
                    aup_mooring_ids = []
                    if aup_id:
                        aup_mooring_ids = [moa.mooring.id for moa in AuthorisedUserPermit.objects.get(id=aup_id).mooringonapproval_set.all()]
                    if vessel_details_id:
                        ## restrict search results to suitable vessels
                        vessel_details = VesselDetails.objects.get(id=vessel_details_id)
                        mooring_filter = Q(
                            Q(name__icontains=search_term) &
                            Q(mooring_bay__id=mooring_bay_id) &
                            Q(vessel_size_limit__gte=vessel_details.vessel_applicable_length) &
                            Q(vessel_draft_limit__gte=vessel_details.vessel_draft) &
                            ~Q(id__in=aup_mooring_ids) &
                            Q(active=True) &
                            Q(mooring_licence__status__in=MooringLicence.STATUSES_AS_CURRENT)  # Make sure this mooring is licensed because an unlicensed mooring would never be allocated to an AU permit.
                        )
                        data = Mooring.authorised_user_moorings.filter(mooring_filter).values('id', 'name', 'mooring_licence')[:num_of_moorings_to_return]
                    else:
                        data = []
                else:
                    mooring_filter = Q(
                        Q(name__icontains=search_term) &
                        Q(active=True) &
                        Q(mooring_licence__status__in=MooringLicence.STATUSES_AS_CURRENT)  # Make sure this mooring is licensed because an unlicensed mooring would never be allocated to an AU permit.
                    )
                    data = Mooring.private_moorings.filter(mooring_filter).values('id', 'name', 'mooring_licence')[:num_of_moorings_to_return]
            # data_transform = [{'id': mooring['id'], 'text': mooring['name']} for mooring in data]
            data_transform = []
            for mooring in data:
                if 'mooring_licence' in mooring and mooring['mooring_licence']:
                    data_transform.append({'id': mooring['id'], 'text': mooring['name'] + ' (licensed)'})
                else:
                    # Should not reach here.  An unlicensed mooring would never be allocated to an AU permit.
                    data_transform.append({'id': mooring['id'], 'text': mooring['name'] + ' (unlicensed)'})
            return Response({"results": data_transform})
        return Response()


class GetVesselRegoNos(views.APIView):
    renderer_classes = [JSONRenderer, ]
    def get(self, request, format=None):
        search_term = request.GET.get('term', '')
        allow_add_new_vessel = request.GET.get('allow_add_new_vessel', 'true')
        allow_add_new_vessel = True if allow_add_new_vessel.lower() == 'true' else False
        proposal_id = request.GET.get('proposal_id', 0)
        proposal = Proposal.objects.get(id=proposal_id)

        if not search_term or not proposal:
            return Response()

        if proposal.proposal_type.code == PROPOSAL_TYPE_NEW:
            # Make sure 'allow_add_new_vessel' is True when new application
            allow_add_new_vessel = True
            data = Vessel.objects.filter(rego_no__icontains=search_term).values('id', 'rego_no')[:10]
        else:
            # Amendment/Renewal
            if proposal.approval:
                existing_vessel_ids = []  # Store id of the vessel that belongs to the proposal.approval
                if proposal.approval.child_obj.code == 'ml':
                    vooas = proposal.approval.child_obj.get_current_vessel_ownership_on_approvals()
                    for vessel_ownership_on_approval in vooas:
                        existing_vessel_ids.append(vessel_ownership_on_approval.vessel_ownership.vessel.id)
                else:
                    v_details = proposal.approval.child_obj.current_proposal.latest_vessel_details
                    v_ownership = proposal.approval.child_obj.current_proposal.vessel_ownership
                    if v_details and not v_ownership.end_date:
                        existing_vessel_ids.append(v_details.vessel.id)

                if allow_add_new_vessel:
                    # Customer wants to add a new vessel
                    # Return all the vessels except existing vessels that belongs to the proposal.approval
                    data = Vessel.objects.filter(rego_no__icontains=search_term).exclude(id__in=existing_vessel_ids).values('id', 'rego_no')[:10]
                else:
                    # Customer wants to edit an existing vessel which belongs to the proposal.approval
                    # Return only existing vessels that belong to the proposal.approval
                    data = Vessel.objects.filter(rego_no__icontains=search_term, id__in=existing_vessel_ids).values('id', 'rego_no')[:10]
            else:
                data = []

        data_transform = [{'id': rego['id'], 'text': rego['rego_no']} for rego in data]
        return Response({"results": data_transform})

class GetCompanyNames(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        search_term = request.GET.get('term', '')
        if search_term:
            data = Company.objects.filter(name__icontains=search_term).values('id', 'name')[:10]
            data_transform = []
            data_transform = [{'id': company['id'], 'text': company['name']} for company in data] 
            return Response({"results": data_transform})
        return Response()


class GetApplicationTypeDescriptions(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        data = cache.get('application_type_descriptions')
        if not data:
            cache.set('application_type_descriptions',Proposal.application_type_descriptions(), settings.LOV_CACHE_TIMEOUT)
            data = cache.get('application_type_descriptions')
        return Response(data)


class GetStickerReplacementFeeItem(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        from mooringlicensing.components.payments_ml.models import FeeItemStickerReplacement

        current_datetime = datetime.now(pytz.timezone(TIME_ZONE))
        fee_item = FeeItemStickerReplacement.get_fee_item_by_date(current_datetime.date())

        return Response({'amount': fee_item.amount, 'incur_gst': fee_item.incur_gst})


class GetPaymentSystemId(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        return Response({'payment_system_id': PAYMENT_SYSTEM_ID})


# class GetApplicantsDict(views.APIView):
#     renderer_classes = [JSONRenderer, ]
#
#     def get(self, request, format=None):
#         applicants = EmailUser.objects.filter(mooringlicensing_proposals__in=Proposal.objects.all()).order_by('first_name', 'last_name').distinct()
#         return Response(EmailUserSerializer(applicants, many=True).data)


class GetApplicationTypeDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        apply_page = request.GET.get('apply_page', 'false')
        apply_page = True if apply_page.lower() in ['true', 'yes', 'y', ] else False
        data = cache.get('application_type_dict')
        if not data:
            cache.set('application_type_dict',Proposal.application_types_dict(apply_page=apply_page), settings.LOV_CACHE_TIMEOUT)
            data = cache.get('application_type_dict')
        return Response(data)


class GetApplicationCategoryDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        apply_page = request.GET.get('apply_page', 'false')
        apply_page = True if apply_page.lower() in ['true', 'yes', 'y', ] else False
        data = cache.get('application_category_dict')
        if not data:
            cache.set('application_category_dict',Proposal.application_categories_dict(apply_page=apply_page), settings.LOV_CACHE_TIMEOUT)
            data = cache.get('application_category_dict')
        return Response(data)


class GetApplicationStatusesDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        data = {}
        if not cache.get('application_internal_statuses_dict') or not cache.get('application_external_statuses_dict'):
            cache.set('application_internal_statuses_dict',[{'code': i[0], 'description': i[1]} for i in Proposal.PROCESSING_STATUS_CHOICES], settings.LOV_CACHE_TIMEOUT)
            cache.set('application_external_statuses_dict',[{'code': i[0], 'description': i[1]} for i in Proposal.CUSTOMER_STATUS_CHOICES if i[0] != 'discarded'], settings.LOV_CACHE_TIMEOUT)
        data['external_statuses'] = cache.get('application_external_statuses_dict')
        data['internal_statuses'] = cache.get('application_internal_statuses_dict')
        return Response(data)


class GetVesselTypesDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        data = cache.get('vessel_type_dict')
        if not data:
            cache.set('vessel_type_dict',[{'code': i[0], 'description': i[1]} for i in VESSEL_TYPES], settings.LOV_CACHE_TIMEOUT)
            data = cache.get('vessel_type_dict')
        return Response(data)


class GetInsuranceChoicesDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        data = cache.get('insurance_choice_dict')
        if not data:
            cache.set('insurance_choice_dict',[{'code': i[0], 'description': i[1]} for i in INSURANCE_CHOICES], settings.LOV_CACHE_TIMEOUT)
            data = cache.get('insurance_choice_dict')
        return Response(data)


class GetMooringStatusesDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request):
        return Response(['Unlicensed', 'Licensed', 'Licence application'])


class GetEmptyList(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        return Response([])


class VersionableModelViewSetMixin(viewsets.ModelViewSet):
    @detail_route(methods=['GET',], detail=True)
    def history(self, request, *args, **kwargs):
        _object = self.get_object()
        _versions = Version.objects.get_for_object(_object)

        _context = {
            'request': request
        }

        _version_serializer = ProposalSerializer([v.object for v in _versions], many=True, context=_context)
        # TODO
        # check pagination
        return Response(_version_serializer.data)


class ProposalFilterBackend(DatatablesFilterBackend):
    def filter_queryset(self, request, queryset, view):

        total_count = queryset.count()
        level = request.GET.get('level', 'external')  # Check where the request comes from
        filter_query = Q()

        try:
            super_queryset = super(ProposalFilterBackend, self).filter_queryset(request, queryset, view).distinct()
            
        except Exception as e:
            logger.exception(f'Failed to filter the queryset.  Error: [{e}]')
        setattr(view, '_datatables_total_count', total_count)

        search_text = request.GET.get('search[value]')
        if search_text:
            #the search conducted by the superclass only accomodates the ProposalApplicant users
            #this misses any new draft proposals, which do not yet have a ProposalApplicant record assigned - so we will do that here
            email_user_ids = list(EmailUser.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
            .filter(
                Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
            ).values_list("id", flat=True))
            #the search also does not accomodate combining first names and last names even with ProposalApplicant - so we will also do that here
            proposal_applicant_proposals = list(ProposalApplicant.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
            .filter(
                Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
            ).values_list("proposal_id", flat=True))
            queryset = queryset.filter(Q(id__in=proposal_applicant_proposals)|Q(submitter__in=email_user_ids))
            queryset = queryset.distinct() | super_queryset    

        mla_list = MooringLicenceApplication.objects.all()
        aua_list = AuthorisedUserApplication.objects.all()
        aaa_list = AnnualAdmissionApplication.objects.all()
        wla_list = WaitingListApplication.objects.all()

        filter_application_type = request.GET.get('filter_application_type')
        if filter_application_type and not filter_application_type.lower() == 'all':
            if filter_application_type == 'mla':
                filter_query &= Q(id__in=mla_list)
            elif filter_application_type == 'aua':
                filter_query &= Q(id__in=aua_list)
            elif filter_application_type == 'aaa':
                filter_query &= Q(id__in=aaa_list)
            elif filter_application_type == 'wla':
                filter_query &= Q(id__in=wla_list)

        filter_application_category = request.GET.get('filter_application_category')
        if filter_application_category and not filter_application_category.lower() == 'all':
            if filter_application_category == 'new':
                filter_query &= Q(proposal_type__code=settings.PROPOSAL_TYPE_NEW)
            elif filter_application_category == 'amendment':
                filter_query &= Q(proposal_type__code=settings.PROPOSAL_TYPE_AMENDMENT)
            elif filter_application_category == 'renewal':
                filter_query &= Q(proposal_type__code=settings.PROPOSAL_TYPE_RENEWAL)

        filter_application_status = request.GET.get('filter_application_status')
        if filter_application_status and not filter_application_status.lower() == 'all':
            if level == 'internal':
                filter_query &= Q(processing_status=filter_application_status)
            else:
                filter_query &= Q(customer_status=filter_application_status)

        filter_applicant_id = request.GET.get('filter_applicant')
        if filter_applicant_id and not filter_applicant_id.lower() == 'all':
            filter_query &= Q(submitter__id=filter_applicant_id)

        # Filter by endorsement
        filter_by_endorsement = request.GET.get('filter_by_endorsement', 'false')
        filter_by_endorsement = True if filter_by_endorsement.lower() in ['true', 'yes', 't', 'y',] else False
        if filter_by_endorsement:
            filter_query &= Q(site_licensee_email__iexact=request.user.email)
        else:
            filter_query &= ~Q(site_licensee_email__iexact=request.user.email)

        # don't show discarded applications
        if not level == 'internal':
            filter_query &= ~Q(customer_status='discarded')

        queryset = queryset.filter(filter_query)
        # getter = request.query_params.get
        # fields = self.get_fields(getter)
        # ordering = self.get_ordering(getter, fields)
        #queryset = queryset.order_by(*ordering)
        fields = self.get_fields(request)
        ordering = self.get_ordering(request, view, fields)
        queryset = queryset.order_by(*ordering)
        if len(ordering):
            #for num, item in enumerate(ordering):
            #    if item == 'lodgement_number':
            #        ordering[num] = 'id'
            #    elif item == '-lodgement_number':
            #        ordering[num] = '-id'
            queryset = queryset.order_by(*ordering)
        else:
            queryset = queryset.order_by('-id')
        
        return queryset


class ProposalRenderer(DatatablesRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if 'view' in renderer_context and hasattr(renderer_context['view'], '_datatables_total_count'):
            data['recordsTotal'] = renderer_context['view']._datatables_total_count
        return super(ProposalRenderer, self).render(data, accepted_media_type, renderer_context)


class ProposalPaginatedViewSet(viewsets.ModelViewSet):
    filter_backends = (ProposalFilterBackend,)
    pagination_class = DatatablesPageNumberPagination
    renderer_classes = (ProposalRenderer,)
    queryset = Proposal.objects.none()
    serializer_class = ListProposalSerializer
    search_fields = ['lodgement_number', ]
    page_size = 10

    def get_queryset(self):
        request_user = self.request.user
        #all = Proposal.objects.all()
        all = Proposal.objects.exclude(migrated=True)

        target_email_user_id = int(self.request.GET.get('target_email_user_id', 0))

        if is_internal(self.request):
            if target_email_user_id:
                # Internal user may be accessing here via search person result. 
                target_user = EmailUser.objects.get(id=target_email_user_id)
                user_orgs = Organisation.objects.filter(delegates__contains=[target_user.id])
                all = all.filter(Q(org_applicant__in=user_orgs) | Q(submitter=target_user.id) | Q(site_licensee_email=target_user.email))
            return all
        elif is_customer(self.request):
            orgs = Organisation.objects.filter(delegates__contains=[request_user.id])
            # user_orgs = [org.id for org in request_user.mooringlicensing_organisations.all()]
            # user_orgs = [org.id for org in orgs]
            # qs = all.filter(Q(org_applicant_id__in=user_orgs) | Q(submitter=request_user.id) | Q(site_licensee_email=request_user.email))
            qs = all.filter(Q(org_applicant__in=orgs) | Q(submitter=request_user.id) | Q(site_licensee_email=request_user.email))
            return qs
        return Proposal.objects.none()

    def list(self, request, *args, **kwargs):
        """
        User is accessing /external/ page
        """
        qs = self.get_queryset()
        qs = self.filter_queryset(qs)

        self.paginator.page_size = qs.count()
        #result_page = self.paginator.paginate_queryset(qs.order_by('-id'), request)
        result_page = self.paginator.paginate_queryset(qs, request)
        serializer = ListProposalSerializer(result_page, context={'request': request}, many=True)
        return self.paginator.get_paginated_response(serializer.data)


class AnnualAdmissionApplicationViewSet(viewsets.ModelViewSet):
    queryset = AnnualAdmissionApplication.objects.none()
    serializer_class = ProposalSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = AnnualAdmissionApplication.objects.all()
            return qs
        elif is_customer(self.request):
            queryset = AnnualAdmissionApplication.objects.filter(Q(proxy_applicant_id=user.id) | Q(submitter=user.id))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return AnnualAdmissionApplication.objects.none()

    def create(self, request, *args, **kwargs):
        proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_NEW)

        obj = AnnualAdmissionApplication.objects.create(
                submitter=request.user.id,
                proposal_type=proposal_type
                )
        logger.info(f'Annual Admission Application: [{obj}] has been created by the user: [{request.user}].')
        obj.log_user_action(f'Annual Admission Application: {obj.lodgement_number} has been created.', request)

        serialized_obj = ProposalSerializer(obj.proposal)
        return Response(serialized_obj.data)


class AuthorisedUserApplicationViewSet(viewsets.ModelViewSet):
    queryset = AuthorisedUserApplication.objects.none()
    serializer_class = ProposalSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = AuthorisedUserApplication.objects.all()
            return qs
        elif is_customer(self.request):
            queryset = AuthorisedUserApplication.objects.filter(Q(proxy_applicant_id=user.id) | Q(submitter=user.id))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return AuthorisedUserApplication.objects.none()

    def create(self, request, *args, **kwargs):
        proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_NEW)

        obj = AuthorisedUserApplication.objects.create(
                submitter=request.user.id,
                proposal_type=proposal_type
                )
        logger.info(f'Authorised User Permit Application: [{obj}] has been created by the user: [{request.user}].')
        obj.log_user_action(f'Authorised User Permit Application: {obj.lodgement_number} has been created.', request)

        serialized_obj = ProposalSerializer(obj.proposal)
        return Response(serialized_obj.data)


class MooringLicenceApplicationViewSet(viewsets.ModelViewSet):
    queryset = MooringLicenceApplication.objects.none()
    serializer_class = ProposalSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = MooringLicenceApplication.objects.all()
            return qs
        elif is_customer(self.request):
            queryset = MooringLicenceApplication.objects.filter(Q(proxy_applicant_id=user.id) | Q(submitter=user.id))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return MooringLicenceApplication.objects.none()

    def create(self, request, *args, **kwargs):
        proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_NEW)
        mooring_id = request.data.get('mooring_id')
        mooring=None
        if mooring_id:
            mooring = Mooring.objects.get(id=mooring_id)

        obj = MooringLicenceApplication.objects.create(
                submitter=request.user.id,
                proposal_type=proposal_type,
                allocated_mooring=mooring,
                )
        logger.info(f'Mooring Licence Application: [{obj}] has been created by the user: [{request.user}].')
        obj.log_user_action(f'Mooring Licence Application: {obj.lodgement_number} has been created.', request)

        serialized_obj = ProposalSerializer(obj.proposal)
        return Response(serialized_obj.data)


class WaitingListApplicationViewSet(viewsets.ModelViewSet):
    queryset = WaitingListApplication.objects.none()
    serializer_class = ProposalSerializer
    lookup_field = 'id'

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = WaitingListApplication.objects.all()
            return qs
        elif is_customer(self.request):
            # queryset = WaitingListApplication.objects.filter(Q(proxy_applicant_id=user.id) | Q(submitter=user.id))
            queryset = WaitingListApplication.objects.filter(Q(proxy_applicant=user.id) | Q(submitter=user.id))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return WaitingListApplication.objects.none()

    def create(self, request, *args, **kwargs):
        proposal_type = ProposalType.objects.get(code=PROPOSAL_TYPE_NEW)

        obj = WaitingListApplication.objects.create(
                submitter=request.user.id,
                proposal_type=proposal_type
                )

        logger.info(f'Waiting List Application: [{obj}] has been created by the user: [{request.user}].')
        obj.log_user_action(f'Waiting List Application: {obj.lodgement_number} has been created.', request)

        serialized_obj = ProposalSerializer(obj.proposal)
        return Response(serialized_obj.data)


class ProposalByUuidViewSet(viewsets.ModelViewSet):
    queryset = Proposal.objects.none()

    def get_object(self):
        uuid = self.kwargs.get('pk')
        return MooringLicenceApplication.objects.get(uuid=uuid)

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    # def process_mooring_report_document(self, request, *args, **kwargs):
    def mooring_report_document(self, request, *args, **kwargs):
        instance = self.get_object()
        action = request.data.get('action')

        # returned_data = process_generic_document(request, instance, document_type='mooring_report_document')
        # if returned_data:
        #     return Response(returned_data)
        # else:
        #     return Response()

        if action == 'list':
            pass
        elif action == 'delete':
            document_id = request.data.get('document_id')
            document = MooringReportDocument.objects.get(
                # proposal=instance,
                id=document_id,
            )
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            if document:
                # original_file_name = document.original_file_name
                # original_file_ext = document.original_file_ext
                original_file_name = document.name
                document.delete()
                # logger.info(f'VesselRegistrationDocument file: {original_file_name}{original_file_ext} has been deleted.')
                logger.info(f'MooringReportDocument file: {original_file_name} has been deleted.')
        elif action == 'cancel':
            pass
        elif action == 'save':
            filename = request.data.get('filename')
            _file = request.data.get('_file')

            filepath = pathlib.Path(filename)

            # Calculate a new unique filename
            if MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE:
                unique_id = uuid.uuid4()
                new_filename = unique_id.hex + filepath.suffix
            else:
                new_filename = filepath.stem + filepath.suffix

            document = MooringReportDocument.objects.create(
                # proposal=instance,
                name=filepath.stem + filepath.suffix
            )
            instance.mooring_report_documents.add(document)
            document._file.save(new_filename, ContentFile(_file.read()))

            logger.info(f'MooringReportDocument file: {filename} has been saved as {document._file.url}')

        # returned_file_data = []
        docs_in_limbo = instance.mooring_report_documents.all()  # Files uploaded when vessel_ownership is unknown
        all_the_docs = docs_in_limbo

        returned_file_data = construct_dict_from_docs(all_the_docs)

        return Response({'filedata': returned_file_data})

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def process_written_proof_document(self, request, *args, **kwargs):
        instance = self.get_object()
        returned_data = process_generic_document(request, instance, document_type='written_proof_document')
        if returned_data:
            return Response(returned_data)
        else:
            return Response()

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def process_signed_licence_agreement_document(self, request, *args, **kwargs):
        instance = self.get_object()
        returned_data = process_generic_document(request, instance, document_type='signed_licence_agreement_document')
        if returned_data:
            return Response(returned_data)
        else:
            return Response()

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def process_proof_of_identity_document(self, request, *args, **kwargs):
        instance = self.get_object()
        returned_data = process_generic_document(request, instance, document_type='proof_of_identity_document')
        if returned_data:
            return Response(returned_data)
        else:
            return Response()

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    # @basic_exception_handler
    def submit(self, request, *args, **kwargs):
        instance = self.get_object()
        logger.info(f'Proposal: [{instance}] has been submitted with UUID...')

        # Make sure the submitter is the same as the applicant.
        is_authorised_to_modify(request, instance)

        errors = []
        if not instance.mooring_report_documents.count():
            errors.append('Copy of current mooring report')
        if not instance.written_proof_documents.count():
            errors.append('Proof of finalised ownership of mooring apparatus')
        if not instance.signed_licence_agreement_documents.count():
            errors.append('Signed licence agreement')
        if not instance.proof_of_identity_documents.count():
            errors.append('Proof of identity')

        if errors:
            errors.insert(0, 'Please attach:')
            raise serializers.ValidationError(errors)

        instance.process_after_submit_other_documents(request)
        return Response()


class ProposalViewSet(viewsets.ModelViewSet):
    queryset = Proposal.objects.none()
    serializer_class = ProposalSerializer
    lookup_field = 'id'

    def get_serializer_class(self):
        if not self.kwargs.get('id').isnumeric():
            # Endorser is accessing this proposal.  We don't want to send all the proposal data.
            return ProposalForEndorserSerializer
        return super(ProposalViewSet, self).get_serializer_class()

    # def retrieve(self, request, *args, **kwargs):
    #     return super(ProposalViewSet, self).retrieve(request, *args, **kwargs)

    def get_object(self):
        id = self.kwargs.get('id')
        logger.info(f'Getting proposal in the ProposalViewSet by the ID: [{id}]...')

        if id.isnumeric():
            obj = super(ProposalViewSet, self).get_object()
        else:
            # When AUP holder accesses this proposal for endorsement 
            uuid = self.kwargs.get('id')
            obj = AuthorisedUserApplication.objects.get(uuid=uuid)
            obj = obj.proposal
        return obj

    def get_queryset(self):
        request_user = self.request.user
        if is_internal(self.request):
            qs = Proposal.objects.all()
            return qs
        elif is_customer(self.request):
            # user_orgs = [org.id for org in request_user.mooringlicensing_organisations.all()]
            # queryset = Proposal.objects.filter(Q(org_applicant_id__in=user_orgs) | Q(submitter=request_user.id) | Q(site_licensee_email=request_user.email))
            user_orgs = [org.id for org in Organisation.objects.filter(delegates__contains=[self.request.user.id])]
            queryset = Proposal.objects.filter(
                Q(org_applicant_id__in=user_orgs) | Q(submitter=request_user.id)
            ).exclude(migrated=True)

            # For the endoser to view the endosee's proposal
            if 'uuid' in self.request.query_params:
                uuid = self.request.query_params.get('uuid', '')
                if uuid:
                    au_obj = AuthorisedUserApplication.objects.filter(uuid=uuid)  # ML also has a uuid field.
                    if au_obj:
                        pro = Proposal.objects.filter(id=au_obj.first().id)
                        # Add the above proposal to the queryset the accessing user can access to
                        queryset = queryset | pro
            return queryset
        logger.warning("User is neither customer nor internal user: {} <{}>".format(request_user.get_full_name(), request_user.email))
        return Proposal.objects.none()

    def internal_serializer_class(self):
       try:
           return InternalProposalSerializer
       except serializers.ValidationError:
           print(traceback.print_exc())
           raise
       except ValidationError as e:
           if hasattr(e,'error_dict'):
               raise serializers.ValidationError(repr(e.error_dict))
           else:
               if hasattr(e,'message'):
                   raise serializers.ValidationError(e.message)
       except Exception as e:
           print(traceback.print_exc())
           raise serializers.ValidationError(str(e))

    @detail_route(methods=['PUT'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def reinstate_wl_allocation(self, request, *args, **kwargs):
        instance = self.get_object()
        if is_internal(request) and instance.child_obj.code == MooringLicenceApplication.code and instance.processing_status in [Proposal.PROCESSING_STATUS_DISCARDED,]:
            # Internal user is accessing
            # Proposal is ML application and the status of it is 'discarded'
            wlallocation = instance.child_obj.reinstate_wl_allocation(request)
            return Response({'lodgement_number': wlallocation.lodgement_number})  # TODO
        else:
            msg = f'This application: [{instance}] does not meet the conditions to put the original WLAllocation to the waiting list queue.'
            logger.warn(msg)
            raise serializers.ValidationError(msg)

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def vessel_rego_document(self, request, *args, **kwargs):
        instance = self.get_object()
        action = request.data.get('action')

        if action == 'list':
            pass
        elif action == 'delete':
            document_id = request.data.get('document_id')
            document = VesselRegistrationDocument.objects.get(
                proposal=instance,
                id=document_id,
            )
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            if document:
                original_file_name = document.original_file_name
                original_file_ext = document.original_file_ext
                document.delete()
                logger.info(f'VesselRegistrationDocument file: {original_file_name}{original_file_ext} has been deleted.')
        elif action == 'cancel':
            pass
        elif action == 'save':
            filename = request.data.get('filename')
            _file = request.data.get('_file')

            filepath = pathlib.Path(filename)
            original_file_name = filepath.stem
            original_file_ext = filepath.suffix

            # Calculate a new unique filename
            if MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE:
                unique_id = uuid.uuid4()
                new_filename = unique_id.hex + original_file_ext
            else:
                new_filename = original_file_name + original_file_ext

            document = VesselRegistrationDocument.objects.create(
                proposal=instance,
                name=filepath.stem + filepath.suffix,
                original_file_name=original_file_name,
                original_file_ext=original_file_ext,
            )
            document._file.save(new_filename, ContentFile(_file.read()))

            logger.info(f'VesselRegistrationDocument file: {filename} has been saved as {document._file.url}')

        docs_in_limbo = instance.temp_vessel_registration_documents.all()  # Files uploaded when vessel_ownership is unknown
        docs = instance.vessel_ownership.vessel_registration_documents.all() if instance.vessel_ownership else VesselRegistrationDocument.objects.none()
        all_the_docs = docs_in_limbo | docs  # Merge two querysets

        returned_file_data = construct_dict_from_docs(all_the_docs)

        return Response({'filedata': returned_file_data})

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    # def process_electoral_roll_document(self, request, *args, **kwargs):
    def electoral_roll_document(self, request, *args, **kwargs):
        instance = self.get_object()
        action = request.data.get('action')

        # returned_data = process_generic_document(request, instance, document_type='electoral_roll_document')
        # if returned_data:
        #     return Response(returned_data)
        # else:
        #     return Response()

        if action == 'list':
            pass
        elif action == 'delete':
            document_id = request.data.get('document_id')
            document = ElectoralRollDocument.objects.get(
                proposal=instance,
                id=document_id,
            )
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            if document:
                # original_file_name = document.original_file_name
                # original_file_ext = document.original_file_ext
                original_file_name = document.name
                document.delete()
                # logger.info(f'VesselRegistrationDocument file: {original_file_name}{original_file_ext} has been deleted.')
                logger.info(f'ElectoralRollDocument file: {original_file_name} has been deleted.')
        elif action == 'cancel':
            pass
        elif action == 'save':
            filename = request.data.get('filename')
            _file = request.data.get('_file')

            filepath = pathlib.Path(filename)

            # Calculate a new unique filename
            if MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE:
                unique_id = uuid.uuid4()
                new_filename = unique_id.hex + filepath.suffix
            else:
                new_filename = filepath.stem + filepath.suffix

            document = ElectoralRollDocument.objects.create(
                proposal=instance,
                name=filepath.stem + filepath.suffix
            )
            document._file.save(new_filename, ContentFile(_file.read()))

            logger.info(f'ElectoralRollDocument file: {filename} has been saved as {document._file.url}')

        # returned_file_data = []
        docs_in_limbo = instance.electoral_roll_documents.all()  # Files uploaded when vessel_ownership is unknown
        all_the_docs = docs_in_limbo

        returned_file_data = construct_dict_from_docs(all_the_docs)

        return Response({'filedata': returned_file_data})

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    # def process_hull_identification_number_document(self, request, *args, **kwargs):
    def hull_identification_number_document(self, request, *args, **kwargs):
        instance = self.get_object()
        action = request.data.get('action')

        # returned_data = process_generic_document(request, instance, document_type='hull_identification_number_document')
        # if returned_data:
        #     return Response(returned_data)
        # else:
        #     return Response()

        if action == 'list':
            pass
        elif action == 'delete':
            document_id = request.data.get('document_id')
            document = HullIdentificationNumberDocument.objects.get(
                proposal=instance,
                id=document_id,
            )
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            if document:
                # original_file_name = document.original_file_name
                # original_file_ext = document.original_file_ext
                original_file_name = document.name
                document.delete()
                # logger.info(f'VesselRegistrationDocument file: {original_file_name}{original_file_ext} has been deleted.')
                logger.info(f'HullIdentificationNumberDocument file: {original_file_name} has been deleted.')
        elif action == 'cancel':
            pass
        elif action == 'save':
            filename = request.data.get('filename')
            _file = request.data.get('_file')

            filepath = pathlib.Path(filename)
            # original_file_name = filepath.stem
            # original_file_ext = filepath.suffix

            # Calculate a new unique filename
            if MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE:
                unique_id = uuid.uuid4()
                # new_filename = unique_id.hex + original_file_ext
                new_filename = unique_id.hex + filepath.suffix
            else:
                # new_filename = original_file_name + original_file_ext
                new_filename = filepath.stem + filepath.suffix

            document = HullIdentificationNumberDocument.objects.create(
                proposal=instance,
                name=filepath.stem + filepath.suffix
                # original_file_name=original_file_name,
                # original_file_ext=original_file_ext,
            )
            # path_format_string = 'proposal/{}/vessel_registration_documents/{}'
            # document._file.save(path_format_string.format(instance.id, new_filename), ContentFile(_file.read()))
            document._file.save(new_filename, ContentFile(_file.read()))

            logger.info(f'HullIdentificationNumberDocument file: {filename} has been saved as {document._file.url}')

        # retrieve temporarily uploaded documents when the proposal is 'draft'
        # returned_file_data = []
        docs_in_limbo = instance.hull_identification_number_documents.all()  # Files uploaded when vessel_ownership is unknown
        # docs = instance.vessel_ownership.vessel_registration_documents.all() if instance.vessel_ownership else VesselRegistrationDocument.objects.none()
        # all_the_docs = docs_in_limbo | docs  # Merge two querysets
        all_the_docs = docs_in_limbo

        returned_file_data = construct_dict_from_docs(all_the_docs)

        return Response({'filedata': returned_file_data})

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    # def process_insurance_certificate_document(self, request, *args, **kwargs):
    def insurance_certificate_document(self, request, *args, **kwargs):
        instance = self.get_object()
        action = request.data.get('action')

        # returned_data = process_generic_document(request, instance, document_type='insurance_certificate_document')
        # if returned_data:
        #     return Response(returned_data)
        # else:
        #     return Response()

        if action == 'list':
            pass
        elif action == 'delete':
            document_id = request.data.get('document_id')
            document = InsuranceCertificateDocument.objects.get(
                proposal=instance,
                id=document_id,
            )
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            if document:
                # original_file_name = document.original_file_name
                # original_file_ext = document.original_file_ext
                original_file_name = document.name
                document.delete()
                # logger.info(f'VesselRegistrationDocument file: {original_file_name}{original_file_ext} has been deleted.')
                logger.info(f'InsuranceCertificateDocument file: {original_file_name} has been deleted.')
        elif action == 'cancel':
            pass
        elif action == 'save':
            filename = request.data.get('filename')
            _file = request.data.get('_file')

            filepath = pathlib.Path(filename)
            # original_file_name = filepath.stem
            # original_file_ext = filepath.suffix

            # Calculate a new unique filename
            if MAKE_PRIVATE_MEDIA_FILENAME_NON_GUESSABLE:
                unique_id = uuid.uuid4()
                # new_filename = unique_id.hex + original_file_ext
                new_filename = unique_id.hex + filepath.suffix
            else:
                # new_filename = original_file_name + original_file_ext
                new_filename = filepath.stem + filepath.suffix

            document = InsuranceCertificateDocument.objects.create(
                proposal=instance,
                name=filepath.stem + filepath.suffix
                # original_file_name=original_file_name,
                # original_file_ext=original_file_ext,
            )
            # path_format_string = 'proposal/{}/vessel_registration_documents/{}'
            # document._file.save(path_format_string.format(instance.id, new_filename), ContentFile(_file.read()))
            document._file.save(new_filename, ContentFile(_file.read()))

            logger.info(f'InsuranceCertificateDocument file: {filename} has been saved as {document._file.url}')

        # retrieve temporarily uploaded documents when the proposal is 'draft'
        # returned_file_data = []
        docs_in_limbo = instance.insurance_certificate_documents.all()  # Files uploaded when vessel_ownership is unknown
        # docs = instance.vessel_ownership.vessel_registration_documents.all() if instance.vessel_ownership else VesselRegistrationDocument.objects.none()
        # all_the_docs = docs_in_limbo | docs  # Merge two querysets
        all_the_docs = docs_in_limbo

        returned_file_data = construct_dict_from_docs(all_the_docs)

        return Response({'filedata': returned_file_data})

    @detail_route(methods=['GET',], detail=True)
    def compare_list(self, request, *args, **kwargs):
        """ Returns the reversion-compare urls --> list"""
        current_revision_id = Version.objects.get_for_object(self.get_object()).first().revision_id
        versions = Version.objects.get_for_object(self.get_object()).select_related("revision__user").filter(Q(revision__comment__icontains='status') | Q(revision_id=current_revision_id))
        version_ids = [i.id for i in versions]
        urls = ['?version_id2={}&version_id1={}'.format(version_ids[0], version_ids[i+1]) for i in range(len(version_ids)-1)]
        return Response(urls)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def action_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.action_logs.all()
        serializer = ProposalUserActionSerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.comms_logs.all()
        serializer = ProposalLogEntrySerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def add_comms_log(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            mutable=request.data._mutable
            request.data._mutable=True
            request.data['proposal'] = u'{}'.format(instance.id)
            request.data['staff'] = u'{}'.format(request.user.id)
            request.data._mutable=mutable
            serializer = ProposalLogEntrySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            comms = serializer.save()
            # Save the files
            for f in request.FILES:
                document = comms.documents.create(
                    name = str(request.FILES[f]),
                    _file = request.FILES[f]
                )
            # End Save Documents

            return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def requirements(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.requirements.all().exclude(is_deleted=True)
        qs=qs.order_by('order')
        serializer = ProposalRequirementSerializer(qs,many=True, context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def amendment_request(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.amendment_requests
        qs = qs.filter(status = 'requested')
        serializer = AmendmentRequestDisplaySerializer(qs,many=True)
        return Response(serializer.data)

    @list_route(methods=['GET',], detail=False)
    def user_list(self, request, *args, **kwargs):
        qs = self.get_queryset().exclude(processing_status='discarded')
        serializer = ListProposalSerializer(qs,context={'request':request}, many=True)
        return Response(serializer.data)

    @list_route(methods=['GET',], detail=False)
    def user_list_paginated(self, request, *args, **kwargs):
        """
        Placing Paginator class here (instead of settings.py) allows specific method for desired behaviour),
        otherwise all serializers will use the default pagination class

        https://stackoverflow.com/questions/29128225/django-rest-framework-3-1-breaks-pagination-paginationserializer
        """
        proposals = self.get_queryset().exclude(processing_status='discarded')
        paginator = DatatablesPageNumberPagination()
        paginator.page_size = proposals.count()
        result_page = paginator.paginate_queryset(proposals, request)
        serializer = ListProposalSerializer(result_page, context={'request':request}, many=True)
        return paginator.get_paginated_response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    def internal_proposal(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = InternalProposalSerializer(instance, context={'request': request})
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def assign_request_user(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.assign_officer(request, request.user)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def assign_to(self, request, *args, **kwargs):
        instance = self.get_object()
        user_id = request.data.get('assessor_id',None)
        user = None
        if not user_id:
            raise serializers.ValidationError('An assessor id is required')
        try:
            user = EmailUser.objects.get(id=user_id)
        except EmailUser.DoesNotExist:
            raise serializers.ValidationError('A user with the id passed in does not exist')
        instance.assign_officer(request,user)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def unassign(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.unassign(request)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def switch_status(self, request, *args, **kwargs):
        instance = self.get_object()
        status = request.data.get('status')
        approver_comment = request.data.get('approver_comment')
        instance.move_to_status(request, status, approver_comment)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def reissue_approval(self, request, *args, **kwargs):
        instance = self.get_object()
        status = request.data.get('status')
        if not status:
            raise serializers.ValidationError('Status is required')
        else:
            if not status in [Proposal.PROCESSING_STATUS_WITH_ASSESSOR,]:
                raise serializers.ValidationError('The status provided is not allowed')
        instance.reissue_approval(request)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def renew_amend_approval_wrapper(self, request, *args, **kwargs):
        instance = self.get_object()
        approval = instance.approval
        ## validation
        renew_amend_conditions = {
            'previous_application': instance,
        }
        existing_proposal_qs=Proposal.objects.filter(**renew_amend_conditions)
        if (existing_proposal_qs and 
            existing_proposal_qs[0].customer_status in [Proposal.CUSTOMER_STATUS_WITH_ASSESSOR, Proposal.CUSTOMER_STATUS_DRAFT,] and
            existing_proposal_qs[0].proposal_type in ProposalType.objects.filter(code__in=[PROPOSAL_TYPE_AMENDMENT, PROPOSAL_TYPE_RENEWAL,])
        ):
            raise ValidationError('A renewal/amendment for this licence has already been lodged.')
        elif not approval or not approval.amend_or_renew:
            raise ValidationError('No licence available for renewal/amendment.')
        ## create renewal or amendment
        if approval and approval.amend_or_renew == 'renew':
            instance = instance.renew_approval(request)
        elif approval and approval.amend_or_renew == 'amend':
            instance = instance.amend_approval(request)
        ## return new application
        #serializer = ProposalSerializer(instance,context={'request':request})
        #serializer_class = self.internal_serializer_class()
        #serializer = serializer_class(instance,context={'request':request})
        #return Response(serializer.data)
        return Response({"id":instance.id})

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def proposed_approval(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ProposedApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.proposed_approval(request, serializer.validated_data)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def approval_level_document(self, request, *args, **kwargs):
        instance = self.get_object()
        instance = instance.assing_approval_level_document(request)
        serializer = InternalProposalSerializer(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def final_approval(self, request, *args, **kwargs):
        print('final_approval() in ProposalViewSet')
        instance = self.get_object()
        serializer = ProposedApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.final_approval(request, serializer.validated_data)
        return Response()

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def proposed_decline(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ProposedDeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.proposed_decline(request,serializer.validated_data)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def final_decline(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ProposedDeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.final_decline(request,serializer.validated_data)
        serializer_class = self.internal_serializer_class()
        serializer = serializer_class(instance, context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['post'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def draft(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()

            # Make sure the submitter is the same as the applicant.
            is_authorised_to_modify(request, instance)

            save_proponent_data(instance,request,self)
            return redirect(reverse('external'))

    @detail_route(methods=['post'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def submit(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()

            logger.info(f'Proposal: [{instance}] has been submitted by the user: [{request.user}].')

            # Ensure status is draft and submitter is same as applicant.
            is_authorised_to_modify(request, instance)
            
            save_proponent_data(instance, request, self)

            is_applicant_address_set(instance)

            return Response()

    @detail_route(methods=['GET',], detail=True)
    def get_max_vessel_length_for_main_component(self, request, *args, **kwargs):
        try:
            from mooringlicensing.components.payments_ml.models import FeeConstructor

            proposal = self.get_object()
            logger.info(f'Calculating the max vessel length for the main component without any additional cost for the Proposal: [{proposal}]...')

            ### test
            max_vessel_length = (0, True)  # (length, include_length)
            get_out_of_loop = False
            while True:
                if proposal.application_fees.all():
                    for application_fee in proposal.application_fees.all():
                        for fee_item_application_fee in application_fee.feeitemapplicationfee_set.all():
                            if fee_item_application_fee.application_fee.proposal.application_type == fee_item_application_fee.fee_item.fee_constructor.application_type:
                                logger.info(f'FeeItemApplicationFee: [{fee_item_application_fee}] is the main component of the proposal: [{proposal}]')
                                length_tuple = fee_item_application_fee.get_max_allowed_length()
                                if max_vessel_length[0] < length_tuple[0] or (max_vessel_length[0] == length_tuple[0] and length_tuple[1] == True):
                                    max_vessel_length = length_tuple
                            else:
                                logger.info(f'FeeItemApplicationFee: [{fee_item_application_fee}] is not the main component of the proposal: [{proposal}]')

                if get_out_of_loop:
                    break

                # Retrieve the previous application
                proposal = proposal.previous_application

                if not proposal:
                    # No previous application exists.  Get out of the loop
                    break
                else:
                    # Previous application exists
                    if proposal.proposal_type.code in [PROPOSAL_TYPE_NEW, PROPOSAL_TYPE_RENEWAL,]:
                        # Previous application is 'new'/'renewal'
                        # In this case, we don't want to go back any further once this proposal is processed in the next loop.  Therefore we set the flat to True
                        get_out_of_loop = True

            res = {'max_length': max_vessel_length[0], 'include_max_length': max_vessel_length[1]}
            logger.info(f'Max vessel length for the main component without any additional cost is [{res}].')

            return Response(res)
            ###

            # if proposal.proposal_type.code in [PROPOSAL_TYPE_AMENDMENT,]:
            #     current_datetime = datetime.now(pytz.timezone(TIME_ZONE))
            #     target_date = proposal.get_target_date(current_datetime.date())
            #
            #     max_amount_paid = proposal.get_max_amount_paid_for_main_component()
            #
            #     fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(proposal.application_type, target_date)
            #     max_length = calculate_max_length(fee_constructor, max_amount_paid, proposal.proposal_type)
            #
            # return Response({'max_length': max_length})

        except Exception as e:
            print(traceback.print_exc())
            if hasattr(e,'message'):
                raise serializers.ValidationError(e.message)

    @detail_route(methods=['GET',], detail=True)
    def get_max_vessel_length_for_aa_component(self, request, *args, **kwargs):
        try:
            from mooringlicensing.components.payments_ml.models import FeeConstructor
            from mooringlicensing.components.main.models import ApplicationType

            proposal = self.get_object()
            logger.info(f'Calculating the max vessel length for the AA component without any additional cost for the Proposal: [{proposal}]...')

            current_datetime = datetime.now(pytz.timezone(TIME_ZONE))
            target_date = proposal.get_target_date(current_datetime.date())

            # Retrieve vessel
            vid = request.GET.get('vid', None)
            vessel = Vessel.objects.get(id=int(vid)) if vid else None

            max_amount_paid = proposal.get_max_amount_paid_for_aa_component(target_date, vessel)

            application_type_aa = ApplicationType.objects.get(code=AnnualAdmissionApplication.code)
            fee_constructor = FeeConstructor.get_fee_constructor_by_application_type_and_date(application_type_aa, target_date)
            max_length = calculate_max_length(fee_constructor, max_amount_paid, proposal.proposal_type)
            res = {'max_length': max_length}

            logger.info(f'Max vessel length for the AA component without any additional cost is [{res}].')

            return Response(res)

        except Exception as e:
            print(traceback.print_exc())
            if hasattr(e,'message'):
                raise serializers.ValidationError(e.message)

    @detail_route(methods=['GET',], detail=True)
    def fetch_vessel(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            if instance.vessel_ownership and not instance.vessel_ownership.end_date:
                #vessel_details = instance.vessel_details
                vessel_details = instance.vessel_details.vessel.latest_vessel_details
                vessel_details_serializer = VesselDetailsSerializer(vessel_details, context={'request': request})
                vessel = vessel_details.vessel
                vessel_serializer = VesselSerializer(vessel)
                vessel_data = vessel_serializer.data
                vessel_ownership_data = {}
                if not instance.editable_vessel_details:
                    vessel_data["rego_no"] = vessel.rego_no
                #else:
                vessel_ownership_data = {}
                if instance.vessel_ownership:
                    vessel_ownership_serializer = VesselOwnershipSerializer(instance.vessel_ownership)
                    vessel_ownership_data = deepcopy(vessel_ownership_serializer.data)
                    vessel_ownership_data["individual_owner"] = False if instance.vessel_ownership.individual_owner else True
                else:
                    vessel_ownership_data["percentage"] = instance.percentage
                    vessel_ownership_data["individual_owner"] = instance.individual_owner
                vessel_data["vessel_details"] = vessel_details_serializer.data
                vessel_data["vessel_ownership"] = vessel_ownership_data
                return Response(vessel_data)
            else:
                return Response()
        except Exception as e:
            print(traceback.print_exc())
            if hasattr(e,'message'):
                raise serializers.ValidationError(e.message)

    def create(self, request, *args, **kwargs):
        raise NotImplementedError("Parent objects should not be created directly")

    def update(self, request, *args, **kwargs):
        try:
            http_status = status.HTTP_200_OK
            instance = self.get_object()

            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data)
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @basic_exception_handler
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        logger.info(f'Proposal: [{instance}] is being destroyed by the user: [{request.user}].')

        if instance.child_obj.application_type_code == MooringLicenceApplication.code and request.query_params.get('action', '') in ['withdraw',]:
            instance.withdraw(request, *args, **kwargs)
        else:
            instance.destroy(request, *args, **kwargs)

        return Response()

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_personal(self, request, *args, **kwargs):
        with transaction.atomic():
            proposal = self.get_object()
            proposal_applicant = ProposalApplicant.objects.get(proposal=proposal)
            data = {}
            dob = request.data.get('dob', '')
            dob = datetime.strptime(dob, '%d/%m/%Y').date() if dob else dob
            data['first_name'] = request.data.get('first_name')
            data['last_name'] = request.data.get('last_name')
            data['dob'] = dob

            serializer = ProposalApplicantSerializer(proposal_applicant, data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            logger.info(f'Personal details of the proposal: {proposal} have been updated with the data: {data}')

            # format dob for ledger
            data['dob'] = dob.strftime('%d/%m/%Y')

            ## Update the ledger
            ret = update_account_details_in_ledger(request, data)

            return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_contact(self, request, *args, **kwargs):
        with transaction.atomic():
            proposal = self.get_object()
            proposal_applicant = ProposalApplicant.objects.get(proposal=proposal)
            data = {}
            if request.data.get('mobile_number', ''):
                data['mobile_number'] = request.data.get('mobile_number')
            if request.data.get('phone_number', ''):
                data['phone_number'] = request.data.get('phone_number')

            serializer = ProposalApplicantSerializer(proposal_applicant, data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            logger.info(f'Contact details of the proposal: {proposal} have been updated with the data: {data}')

            ## Update the ledger
            ret = update_account_details_in_ledger(request, data)

            return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_address(self, request, *args, **kwargs):
        with transaction.atomic():
            proposal = self.get_object()
            proposal_applicant = ProposalApplicant.objects.get(proposal=proposal)
            data = {}
            if 'residential_line1' in request.data:
                data['residential_line1'] = request.data.get('residential_line1')
            if 'residential_locality' in request.data:
                data['residential_locality'] = request.data.get('residential_locality')
            if 'residential_state' in request.data:
                data['residential_state'] = request.data.get('residential_state')
            if 'residential_postcode' in request.data:
                data['residential_postcode'] = request.data.get('residential_postcode')
            if 'residential_country' in request.data:
                data['residential_country'] = request.data.get('residential_country')
            if request.data.get('postal_same_as_residential'):
                data['postal_same_as_residential'] = True
                data['postal_line1'] = ''
                data['postal_locality'] = ''
                data['postal_state'] = ''
                data['postal_postcode'] = ''
                data['postal_country'] = data['residential_country']
            else:
                data['postal_same_as_residential'] = False
                if 'postal_line1' in request.data:
                    data['postal_line1'] = request.data.get('postal_line1')
                if 'postal_locality' in request.data:
                    data['postal_locality'] = request.data.get('postal_locality')
                if 'postal_state' in request.data:
                    data['postal_state'] = request.data.get('postal_state')
                if 'postal_postcode' in request.data:
                    data['postal_postcode'] = request.data.get('postal_postcode')
                if 'postal_country' in request.data:
                    data['postal_country'] = request.data.get('postal_country')

            serializer = ProposalApplicantSerializer(proposal_applicant, data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            logger.info(f'Address details of the proposal: {proposal} have been updated with the data: {data}')

            # Construct data for ledger
            data_for_ledger = {
                'residential_address': {}, 
                'postal_address': {}
            }
            for key, value in data.items():
                if 'postal' in key:
                    data_for_ledger['postal_address'][key] = value
                elif 'residential' in key:
                    data_for_ledger['residential_address'][key] = value

            ## Update the ledger
            ret = update_account_details_in_ledger(request, data_for_ledger)

            return Response(serializer.data)


class ProposalRequirementViewSet(viewsets.ModelViewSet):
    queryset = ProposalRequirement.objects.none()
    serializer_class = ProposalRequirementSerializer

    def get_queryset(self):
        # qs = ProposalRequirement.objects.all().exclude(is_deleted=True)
        # return qs
        queryset = ProposalRequirement.objects.none()
        user = self.request.user
        if is_internal(self.request):
            queryset = ProposalRequirement.objects.all().exclude(is_deleted=True)
        elif is_customer(self.request):
            # queryset = ProposalRequirement.objects.filter(Q(proxy_applicant_id=user.id) | Q(proposal__submitter=user.id))
            # return queryset
            user_orgs = [org.id for org in Organisation.objects.filter(delegates__contains=[self.request.user.id])]
            queryset = ProposalRequirement.objects.filter(
                Q(proposal__org_applicant_id__in=user_orgs) | Q(proposal__submitter=user.id)
            )
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def move_up(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.up()
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def move_down(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.down()
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def discard(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_deleted = True
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def delete_document(self, request, *args, **kwargs):
        instance = self.get_object()
        RequirementDocument.objects.get(id=request.data.get('id')).delete()
        return Response([dict(id=i.id, name=i.name,_file=i._file.url) for i in instance.requirement_documents.all()])

    @basic_exception_handler
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @basic_exception_handler
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data= request.data)
        serializer.is_valid(raise_exception = True)
        instance = serializer.save()
        return Response(serializer.data)


class ProposalStandardRequirementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProposalStandardRequirement.objects.all()
    serializer_class = ProposalStandardRequirementSerializer

    def get_queryset(self):
        from mooringlicensing.components.main.models import ApplicationType

        qs = ProposalStandardRequirement.objects.none()
        user = self.request.user
        if is_internal(self.request) or is_customer(self.request):
            application_type_code = self.request.query_params.get('application_type_code', '')
            queries = Q(application_type__isnull=True)
            if application_type_code:
                application_type = ApplicationType.objects.get(code=application_type_code)
                queries |= Q(application_type=application_type)
            qs = ProposalStandardRequirement.objects.exclude(obsolete=True).filter(queries)
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        search = request.GET.get('search')
        if search:
            queryset = queryset.filter(text__icontains=search)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


# class BackToAssessorViewSet(viewsets.ModelViewSet):
#     queryset = BackToAssessor.objects.all()
#     serializer_class = BackToAssessorSerializer
#
#     @basic_exception_handler
#     def create(self, request, *args, **kwargs):
#         data = request.data
#         details = request.data.get('details')
#         proposal = request.data.get('proposal')
#         data['details'] = details
#         data['proposal'] = proposal['id']
#
#         serializer = self.get_serializer(data=data)
#         serializer.is_valid(raise_exception = True)
#         instance = serializer.save()
#
#         instance.generate_amendment(request)
#         serializer = self.get_serializer(instance)
#
#         return Response(serializer.data)

class AmendmentRequestViewSet(viewsets.ModelViewSet):
    queryset = AmendmentRequest.objects.all()
    serializer_class = AmendmentRequestSerializer

    def get_queryset(self):
        queryset = AmendmentRequest.objects.none()
        user = self.request.user
        if is_internal(self.request):
            queryset = AmendmentRequest.objects.all()
        elif is_customer(self.request):
            user_orgs = [org.id for org in Organisation.objects.filter(delegates__contains=[self.request.user.id])]
            queryset = AmendmentRequest.objects.filter(
                Q(proposal__org_applicant_id__in=user_orgs) | Q(proposal__submitter=user.id)
            )
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @basic_exception_handler
    def create(self, request, *args, **kwargs):
        data = request.data
        reason_id = request.data.get('reason_id')
        proposal = request.data.get('proposal')
        data['reason'] = reason_id
        data['proposal'] = proposal['id']

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception = True)
        instance = serializer.save()
        logger.info(f'New AmendmentRequest: [{instance}] has been created.')

        instance.generate_amendment(request)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class AmendmentRequestReasonChoicesView(views.APIView):

    renderer_classes = [JSONRenderer,]
    def get(self,request, format=None):
        choices_list = []
        choices=AmendmentReason.objects.all()
        if choices:
            for c in choices:
                choices_list.append({'key': c.id,'value': c.reason})
        return Response(choices_list)


class SearchKeywordsView(views.APIView):
    renderer_classes = [JSONRenderer,]
    def post(self,request, format=None):
        qs = []
        searchWords = request.data.get('searchKeywords')
        searchProposal = request.data.get('searchProposal')
        searchApproval = request.data.get('searchApproval')
        searchCompliance = request.data.get('searchCompliance')
        if searchWords:
            qs= searchKeyWords(searchWords, searchProposal, searchApproval, searchCompliance)
        serializer = SearchKeywordSerializer(qs, many=True)
        return Response(serializer.data)

class SearchReferenceView(views.APIView):
    renderer_classes = [JSONRenderer,]
    def post(self,request, format=None):
        try:
            qs = []
            reference_number = request.data.get('reference_number')
            if reference_number:
                qs= search_reference(reference_number)
            serializer = SearchReferenceSerializer(qs)
            return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            if hasattr(e,'error_dict'):
                raise serializers.ValidationError(repr(e.error_dict))
            else:
                print(e)
                if hasattr(e,'message'):
                    raise serializers.ValidationError(e.message)
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))


class VesselOwnershipViewSet(viewsets.ModelViewSet):
    queryset = VesselOwnership.objects.all().order_by('id')
    serializer_class = VesselOwnershipSerializer

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = VesselOwnership.objects.all().order_by('id')
            return qs
        elif is_customer(self.request):
            queryset = VesselOwnership.objects.filter(Q(owner__in=Owner.objects.filter(Q(emailuser=user.id))))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return VesselOwnership.objects.none()

    @detail_route(methods=['POST'], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def process_vessel_registration_document(self, request, *args, **kwargs):
        instance = self.get_object()
        returned_data = process_generic_document(request, instance, document_type='vessel_registration_document')
        if returned_data:
            return Response(returned_data)
        else:
            return Response()

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def lookup_vessel_ownership(self, request, *args, **kwargs):
        vo = self.get_object()
        vessel_details = vo.vessel.latest_vessel_details
        vessel_details_serializer = VesselDetailsSerializer(vessel_details, context={'request': request})
        vessel = vo.vessel
        vessel_serializer = VesselSerializer(vessel)
        vessel_data = vessel_serializer.data
        vessel_data["vessel_details"] = vessel_details_serializer.data

        # vessel_ownership
        vessel_ownership_data = {}
        vessel_ownership_serializer = VesselOwnershipSerializer(vo)
        vessel_ownership_data = deepcopy(vessel_ownership_serializer.data)
        vessel_ownership_data["individual_owner"] = True if vo.individual_owner else False
        vessel_data["vessel_ownership"] = vessel_ownership_data
        return Response(vessel_data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def record_sale(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            sale_date = request.data.get('sale_date')

            logger.info(f'Recording vessel sale of the vessel_ownership: [{instance}] with the sale_date: {sale_date}')

            if sale_date:
                if not instance.end_date:
                    # proposals with instance copied to listed_vessels
                    for proposal in instance.listed_on_proposals.all():
                        if proposal.processing_status not in [Proposal.PROCESSING_STATUS_DISCARDED, Proposal.PROCESSING_STATUS_APPROVED, Proposal.PROCESSING_STATUS_DECLINED,]:
                            raise serializers.ValidationError(
                                    "You cannot record the sale of this vessel at this time as application {} that lists this vessel is still in progress.".format(proposal.lodgement_number)
                                    )
                    # submitted proposals with instance == proposal.vessel_ownership
                    for proposal in instance.proposal_set.all():
                        if proposal.processing_status not in [Proposal.PROCESSING_STATUS_DISCARDED, Proposal.PROCESSING_STATUS_APPROVED, Proposal.PROCESSING_STATUS_DECLINED,]:
                            raise serializers.ValidationError(
                                    "You cannot record the sale of this vessel at this time as application {} that lists this vessel is still in progress.".format(proposal.lodgement_number)
                                    )

                ## setting the end_date "removes" the vessel from current Approval records
                serializer = SaveVesselOwnershipSaleDateSerializer(instance, {"end_date": sale_date})
                serializer.is_valid(raise_exception=True)
                serializer.save()
                logger.info(f'Vessel sold: VesselOwnership: [{instance}] has been updated with the end_date: [{sale_date}].')
                # send_vessel_sale_notification_mail(request, instance)

                ## collect impacted Approvals
                approval_list = []
                for prop in instance.proposal_set.filter(approval__status=Approval.APPROVAL_STATUS_CURRENT):
                    if (type(prop.approval.child_obj) in [WaitingListAllocation, AnnualAdmissionPermit, AuthorisedUserPermit] and
                            prop.approval not in approval_list):
                        approval_list.append(prop.approval)
                ## collect ML
                for voa in VesselOwnershipOnApproval.objects.filter(vessel_ownership=instance):
                    if voa.approval not in approval_list:
                        approval_list.append(voa.approval)

                ## change Sticker status
                stickers_to_be_returned = []
                for approval in approval_list:
                    # Generate a new licence/permit document
                    approval.generate_doc(False)

                    # Update sticker status
                    stickers_updated = []
                    for a_sticker in instance.sticker_set.filter(status__in=[Sticker.STICKER_STATUS_CURRENT, Sticker.STICKER_STATUS_AWAITING_PRINTING]):
                        a_sticker.status = Sticker.STICKER_STATUS_TO_BE_RETURNED
                        a_sticker.save()
                        logger.info(f'Status of the sticker: {a_sticker} has been changed to {Sticker.STICKER_STATUS_TO_BE_RETURNED}')

                        stickers_to_be_returned.append(a_sticker)
                        stickers_updated.append(a_sticker)
                    for a_sticker in instance.sticker_set.filter(status__in=[Sticker.STICKER_STATUS_READY, Sticker.STICKER_STATUS_NOT_READY_YET]):
                        # vessel sold before the sticker is picked up by cron for export (very rarely happens)
                        a_sticker.status = Sticker.STICKER_STATUS_CANCELLED
                        a_sticker.save()
                        logger.info(f'Status of the sticker: {a_sticker} has been changed to {Sticker.STICKER_STATUS_CANCELLED}')

                        stickers_updated.append(a_sticker)

                    # Update MooringOnApproval
                    moas = MooringOnApproval.objects.filter(sticker__in=stickers_updated)
                    for moa in moas:
                        moa.sticker = None
                        moa.save()
                        logger.info(f'Sticker:None is set to the MooringOnApproval: {moa}')

                    # write approval history
                    approval.write_approval_history('Vessel sold by owner')
                    if approval.code == WaitingListAllocation.code:
                        send_reissue_wla_after_sale_recorded_email(approval, request, instance, stickers_to_be_returned)
                    elif approval.code == AnnualAdmissionPermit.code:
                        send_reissue_aap_after_sale_recorded_email(approval, request, instance, stickers_to_be_returned)
                    elif approval.code == AuthorisedUserPermit.code:
                        send_reissue_aup_after_sale_recorded_email(approval, request, instance, stickers_to_be_returned)
                    elif approval.code == MooringLicence.code:
                        send_reissue_ml_after_sale_recorded_email(approval, request, instance, stickers_to_be_returned)

            else:
                raise serializers.ValidationError("Missing information: You must specify a sale date")
            return Response()

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def fetch_sale_date(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = VesselOwnershipSaleDateSerializer(instance)
        return Response(serializer.data)


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.all().order_by('id')
    serializer_class = CompanySerializer

    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = Company.objects.all().order_by('id')
            return qs
        elif is_customer(self.request):
            companyownerships = CompanyOwnership.objects.filter(Q(vessel_ownerships__in=VesselOwnership.objects.filter(Q(owner__in=Owner.objects.filter(Q(emailuser=user.id))))))
            queryset = Company.objects.filter(Q(companyownership__in=companyownerships))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return Company.objects.none()

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def lookup_company_ownership(self, request, *args, **kwargs):
        company = self.get_object()
        ## discover common vessel ownership
        vessel_id = request.data.get('vessel_id')
        co_list = []
        company_data = CompanySerializer(company).data
        empty_co = {"company": company_data}
        if vessel_id:
            co_qs = CompanyOwnership.objects.filter(vessel=Vessel.objects.get(id=vessel_id), company=company)
            # add business rules
            for co in co_qs.order_by('updated'):
                co_list.append(co)
            if co_list:
                co = co_list[0]
                serializer = CompanyOwnershipSerializer(co)
                return Response(serializer.data)
            else:
                return Response(empty_co)
        return Response(empty_co)


class CompanyOwnershipViewSet(viewsets.ModelViewSet):
    queryset = CompanyOwnership.objects.all().order_by('id')
    serializer_class = CompanyOwnershipSerializer
    
    def get_queryset(self):
        user = self.request.user
        if is_internal(self.request):
            qs = CompanyOwnership.objects.all().order_by('id')
            return qs
        elif is_customer(self.request):
            queryset = CompanyOwnership.objects.filter(Q(vessel_ownerships__in=VesselOwnership.objects.filter(Q(owner__in=Owner.objects.filter(Q(emailuser=user.id))))))
            return queryset
        logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return CompanyOwnership.objects.none()

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def lookup_company_ownership(self, request, *args, **kwargs):
        vessel = self.get_object()
        vessel_details = vessel.latest_vessel_details
        vessel_details_serializer = VesselDetailsSerializer(vessel_details, context={'request': request})
        vessel_serializer = VesselSerializer(vessel)
        vessel_data = vessel_serializer.data
        vessel_data["vessel_details"] = vessel_details_serializer.data

        # vessel_ownership
        vessel_ownership_data = {}
        # check if this emailuser has a matching record for this vessel
        owner_qs = Owner.objects.filter(emailuser=request.user)
        if owner_qs:
            owner = owner_qs[0]
            vo_qs = vessel.vesselownership_set.filter(owner=owner)
            if vo_qs:
                vessel_ownership = vo_qs[0]
                vessel_ownership_serializer = VesselOwnershipSerializer(vessel_ownership)
                vessel_ownership_data = deepcopy(vessel_ownership_serializer.data)
                vessel_ownership_data["individual_owner"] = False if vessel_ownership.company_ownership else True
        vessel_data["vessel_ownership"] = vessel_ownership_data
        return Response(vessel_data)


class VesselViewSet(viewsets.ModelViewSet):
    queryset = Vessel.objects.all().order_by('id')
    serializer_class = VesselSerializer

    def get_queryset(self):
        # return super().get_queryset()
        queryset = Vessel.objects.none()
        user = self.request.user
        if is_internal(self.request):
            queryset = Vessel.objects.all().order_by('id')
        elif is_customer(self.request):
            # user_orgs = [org.id for org in Organisation.objects.filter(delegates__contains=[self.request.user.id])]
            # queryset = Vessel.objects.filter(
            #     Q(proposal__org_applicant_id__in=user_orgs) | Q(proposal__submitter=user.id)
            # )
            owner = Owner.objects.filter(emailuser=user.id)
            if owner:
                queryset = owner[0].vessels.all()
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def find_related_bookings(self, request, *args, **kwargs):
        # return Response({})
        vessel = self.get_object()
        booking_date_str = request.data.get("selected_date")
        booking_date = None
        if booking_date_str:
            booking_date = datetime.strptime(booking_date_str, '%d/%m/%Y').date()
            booking_date = booking_date.strftime('%Y-%m-%d')
        else:
            booking_date = datetime.now().strftime('%Y-%m-%d')
        data = get_bookings(booking_date, vessel.rego_no.upper())
        data = get_bookings(booking_date=booking_date, rego_no=vessel.rego_no.upper(), mooring_id=None)
        return Response(data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def find_related_approvals(self, request, *args, **kwargs):
        vessel = self.get_object()
        selected_date_str = request.data.get("selected_date")
        selected_date = None
        if selected_date_str:
            selected_date = datetime.strptime(selected_date_str, '%d/%m/%Y').date()
        approval_list = []
        vd_set = VesselDetails.objects.filter(vessel=vessel)
        if selected_date:
            for vd in vd_set:
                for prop in vd.proposal_set.all():
                    if (
                        prop.approval and 
                        selected_date >= prop.approval.start_date and
                        selected_date <= prop.approval.expiry_date and
                        # ensure vessel has not been sold
                        prop.vessel_ownership and not prop.vessel_ownership.end_date
                    ):
                        if prop.approval not in approval_list:
                            approval_list.append(prop.approval)
        else:
            for vd in vd_set:
                for prop in vd.proposal_set.all():
                    if (
                        prop.approval and 
                        prop.approval.status == 'current' and
                        # ensure vessel has not been sold
                        prop.vessel_ownership and not prop.vessel_ownership.end_date
                    ):
                        if prop.approval not in approval_list:
                            approval_list.append(prop.approval)

        serializer = LookupApprovalSerializer(approval_list, many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def lookup_vessel_ownership(self, request, *args, **kwargs):
        vessel = self.get_object()
        serializer = VesselFullOwnershipSerializer(vessel.filtered_vesselownership_set.all(), many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.comms_logs.all()
        serializer = VesselLogEntrySerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def add_comms_log(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            mutable=request.data._mutable
            request.data._mutable=True
            request.data['vessel'] = u'{}'.format(instance.id)
            request.data._mutable=mutable
            serializer = VesselLogEntrySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            comms = serializer.save()
            # Save the files
            for f in request.FILES:
                document = comms.documents.create(
                    name = str(request.FILES[f]),
                    _file = request.FILES[f]
                )
            # End Save Documents

            return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def action_log(self, request, *args, **kwargs):
        return Response([])

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def lookup_individual_ownership(self, request, *args, **kwargs):
        vessel = self.get_object()
        owner_set = Owner.objects.filter(emailuser=request.user.id)
        if owner_set:
            # vo_set = vessel.filtered_vesselownership_set.filter(owner=owner_set[0], vessel=vessel, company_ownership=None)
            # vo_set = vessel.filtered_vesselownership_set.filter(owner=owner_set[0], vessel=vessel).exclude(company_ownerships__status=CompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED)
            vo_set = vessel.filtered_vesselownership_set.filter(owner=owner_set[0], vessel=vessel).exclude(company_ownerships__vesselownershipcompanyownership__status=VesselOwnershipCompanyOwnership.COMPANY_OWNERSHIP_STATUS_APPROVED)
            if vo_set:
                serializer = VesselOwnershipSerializer(vo_set[0])
                return Response(serializer.data)
            else:
                return Response()
        return Response()

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def full_details(self, request, *args, **kwargs):
        vessel = self.get_object()
        return Response(VesselFullSerializer(vessel).data)
 
    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def lookup_vessel(self, request, *args, **kwargs):
        vessel = self.get_object()
        vessel_details = vessel.latest_vessel_details
        vessel_details_serializer = VesselDetailsSerializer(vessel_details, context={'request': request})
        vessel_serializer = VesselSerializer(vessel)
        vessel_data = vessel_serializer.data
        vessel_data["vessel_details"] = vessel_details_serializer.data

        # vessel_ownership
        vessel_ownership_data = {}
        # check if this emailuser has a matching record for this vessel
        owner_qs = Owner.objects.filter(emailuser=request.user.id)
        if owner_qs:
            owner = owner_qs[0]
            vo_qs = vessel.vesselownership_set.filter(owner=owner)
            if vo_qs:
                vessel_ownership = vo_qs[0]
                vessel_ownership_serializer = VesselOwnershipSerializer(vessel_ownership)
                vessel_ownership_data = deepcopy(vessel_ownership_serializer.data)
                vessel_ownership_data["individual_owner"] = True if vessel_ownership.individual_owner else False
        vessel_data["vessel_ownership"] = vessel_ownership_data
        return Response(vessel_data)

    @list_route(methods=['GET',], detail=False)
    def list_internal(self, request, *args, **kwargs):
        search_text = request.GET.get('search[value]', '')

        owner_qs = None
        target_email_user_id = int(self.request.GET.get('target_email_user_id', 0))
        if target_email_user_id:
            target_user = EmailUser.objects.get(id=target_email_user_id)
            owner_qs = Owner.objects.filter(emailuser=target_user.id)

        if owner_qs:
            owner = owner_qs[0]
            vessel_ownership_list = owner.vesselownership_set.all()

            # rewrite following for vessel_ownership_list
            if search_text:
                search_text = search_text.lower()
                search_text_vessel_ownership_ids = []
                matching_vessel_type_choices = [choice[0] for choice in VESSEL_TYPES if search_text in choice[1].lower()]
                for vo in vessel_ownership_list:
                    vd = vo.vessel.latest_vessel_details
                    if (search_text in (vd.vessel_name.lower() if vd.vessel_name else '') or
                            search_text in (vd.vessel.rego_no.lower() if vd.vessel.rego_no.lower() else '') or
                            vd.vessel_type in matching_vessel_type_choices or
                            search_text in vo.end_date.strftime('%d/%m/%Y')
                    ):
                        search_text_vessel_ownership_ids.append(vo.id)
                vessel_ownership_list = [vo for vo in vessel_ownership_list if vo.id in search_text_vessel_ownership_ids]

            serializer = ListVesselOwnershipSerializer(vessel_ownership_list, context={'request': request}, many=True)
            return Response(serializer.data)
        else:
            return Response([])

    @list_route(methods=['GET',], detail=False)
    def list_external(self, request, *args, **kwargs):
        search_text = request.GET.get('search[value]', '')
        owner_qs = Owner.objects.filter(emailuser=request.user.id)
        if owner_qs:
            owner = owner_qs[0]
            vessel_ownership_list = owner.vesselownership_set.all()

            # rewrite following for vessel_ownership_list
            if search_text:
                search_text = search_text.lower()
                search_text_vessel_ownership_ids = []
                matching_vessel_type_choices = [choice[0] for choice in VESSEL_TYPES if search_text in choice[1].lower()]
                for vo in vessel_ownership_list:
                    vd = vo.vessel.latest_vessel_details
                    if (search_text in (vd.vessel_name.lower() if vd.vessel_name else '') or
                        search_text in (vd.vessel.rego_no.lower() if vd.vessel.rego_no.lower() else '') or
                        vd.vessel_type in matching_vessel_type_choices or
                        search_text in vo.end_date.strftime('%d/%m/%Y')
                        ):
                        search_text_vessel_ownership_ids.append(vo.id)
                vessel_ownership_list = [vo for vo in vessel_ownership_list if vo.id in search_text_vessel_ownership_ids]
            
            index = 0
            for vo in vessel_ownership_list:
                logger.debug(f'vessel_ownership [{index}]: [{vo}].')
                index += 1

            serializer = ListVesselOwnershipSerializer(vessel_ownership_list, context={'request': request}, many=True)
            return Response(serializer.data)
        else:
            return Response([])


class MooringBayViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MooringBay.objects.none()
    serializer_class = MooringBaySerializer

    def get_queryset(self):
        # return MooringBay.objects.filter(active=True)

        queryset = MooringBay.objects.none()
        user = self.request.user
        if is_internal(self.request) or is_customer(self.request):
            queryset = MooringBay.objects.filter(active=True)
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @list_route(methods=['GET',], detail=False)
    def lookup(self, request, *args, **kwargs):
        qs = self.get_queryset()
        response_data = [{"id":None,"name":"","mooring_bookings_id":None}]
        for mooring in qs:
            response_data.append(MooringBaySerializer(mooring).data)
        return Response(response_data)


class MooringFilterBackend(DatatablesFilterBackend):
    def filter_queryset(self, request, queryset, view):
        total_count = queryset.count()
        # filter_mooring_status
        filter_mooring_status = request.GET.get('filter_mooring_status')
        if filter_mooring_status and not filter_mooring_status.lower() == 'all':
            filtered_ids = [m.id for m in Mooring.objects.all() if m.status.lower() == filter_mooring_status.lower()]
            queryset = queryset.filter(id__in=filtered_ids)

        filter_mooring_bay = request.GET.get('filter_mooring_bay')
        if filter_mooring_bay and not filter_mooring_bay.lower() == 'all':
            queryset = queryset.filter(mooring_bay_id=filter_mooring_bay)

        # getter = request.query_params.get
        # fields = self.get_fields(getter)
        # ordering = self.get_ordering(getter, fields)
        fields = self.get_fields(request)
        ordering = self.get_ordering(request, view, fields)
        queryset = queryset.order_by(*ordering)
        if len(ordering):
            queryset = queryset.order_by(*ordering)

        try:
            super_queryset = super(MooringFilterBackend, self).filter_queryset(request, queryset, view)

            # Custom search
            search_text = request.GET.get('search[value]')  # This has a search term.
            if search_text:
                # email_user_ids = EmailUser.objects.filter(Q(first_name__icontains=search_term) | Q(last_name__icontains=search_term) | Q(email__icontains=search_term)).values_list('id', flat=True)
                # User can search by a fullname, too
                email_user_ids = list(EmailUser.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
                .filter(
                    Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
                ).values_list("id", flat=True))
                proposal_applicant_proposals = list(ProposalApplicant.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
                .filter(
                    Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
                ).values_list("proposal_id", flat=True))

                q_set = queryset.filter(Q(mooring_licence__approval__current_proposal__id__in=proposal_applicant_proposals)|Q(mooring_licence__submitter__in=email_user_ids))

                queryset = super_queryset.union(q_set)

            return queryset


        except Exception as e:
            print(e)
        setattr(view, '_datatables_total_count', total_count)
        return queryset


class MooringRenderer(DatatablesRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if 'view' in renderer_context and hasattr(renderer_context['view'], '_datatables_total_count'):
            data['recordsTotal'] = renderer_context['view']._datatables_total_count
        return super(MooringRenderer, self).render(data, accepted_media_type, renderer_context)


class MooringPaginatedViewSet(viewsets.ModelViewSet):
    filter_backends = (MooringFilterBackend,)
    pagination_class = DatatablesPageNumberPagination
    renderer_classes = (MooringRenderer,)
    queryset = Mooring.objects.none()
    serializer_class = ListMooringSerializer
    page_size = 10

    def get_queryset(self):
        request_user = self.request.user
        qs = Mooring.objects.none()

        if is_internal(self.request):
            qs = Mooring.private_moorings.filter(active=True)

        return qs

    @list_route(methods=['GET',], detail=False)
    def list_internal(self, request, *args, **kwargs):
        qs = self.get_queryset()
        qs = self.filter_queryset(qs)

        self.paginator.page_size = qs.count()
        result_page = self.paginator.paginate_queryset(qs, request)
        serializer = ListMooringSerializer(result_page, context={'request': request}, many=True)
        return self.paginator.get_paginated_response(serializer.data)


class MooringViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Mooring.objects.none()
    serializer_class = MooringSerializer

    def get_queryset(self):
        # return Mooring.objects.filter(active=True)

        queryset = Mooring.objects.none()
        user = self.request.user
        if is_internal(self.request) or is_customer(self.request):
            queryset = Mooring.objects.filter(active=True)
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def find_related_bookings(self, request, *args, **kwargs):
        mooring = self.get_object()
        booking_date_str = request.data.get("selected_date")
        booking_date = None
        if booking_date_str:
            booking_date = datetime.strptime(booking_date_str, '%d/%m/%Y').date()
            booking_date = booking_date.strftime('%Y-%m-%d')
        else:
            booking_date = datetime.now().strftime('%Y-%m-%d')
        data = get_bookings(booking_date=booking_date, rego_no=None, mooring_id=mooring.mooring_bookings_id)
        return Response(data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def find_related_approvals(self, request, *args, **kwargs):
        mooring = self.get_object()
        selected_date_str = request.data.get("selected_date")
        selected_date = None
        if selected_date_str:
            selected_date = datetime.strptime(selected_date_str, '%d/%m/%Y').date()
        if selected_date:
            approval_list = [approval for approval in mooring.approval_set.filter(start_date__lte=selected_date, expiry_date__gte=selected_date)]
        else:
            approval_list = [approval for approval in mooring.approval_set.filter(status=Approval.APPROVAL_STATUS_CURRENT)]
        if mooring.mooring_licence and mooring.mooring_licence.status == Approval.APPROVAL_STATUS_CURRENT:
            approval_list.append(mooring.mooring_licence.approval)

        serializer = LookupApprovalSerializer(list(set(approval_list)), many=True, context={'mooring': mooring})
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.comms_logs.all()
        serializer = MooringLogEntrySerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def add_comms_log(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            mutable=request.data._mutable
            request.data._mutable=True
            request.data['mooring'] = u'{}'.format(instance.id)
            request.data._mutable=mutable
            serializer = MooringLogEntrySerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            comms = serializer.save()
            # Save the files
            for f in request.FILES:
                document = comms.documents.create(
                    name = str(request.FILES[f]),
                    _file = request.FILES[f]
                )
            # End Save Documents

            return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def action_log(self, request, *args, **kwargs):
        return Response([])

    @list_route(methods=['GET',], detail=False)
    @basic_exception_handler
    def internal_list(self, request, *args, **kwargs):
        # add security
        mooring_qs = Mooring.private_moorings.filter(active=True)
        serializer = ListMooringSerializer(mooring_qs, many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def fetch_mooring_name(self, request, *args, **kwargs):
        # add security
        instance = self.get_object()
        return Response({"name": instance.name})

