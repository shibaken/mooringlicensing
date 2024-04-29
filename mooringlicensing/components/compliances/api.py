import logging
import traceback
# import os
# import datetime
# import base64
# import geojson
from rest_framework_datatables.filters import DatatablesFilterBackend
from rest_framework_datatables.renderers import DatatablesRenderer
# from wsgiref.util import FileWrapper
from django.db.models import Q, Min, CharField, Value
from django.db.models.functions import Concat
from django.db import transaction
# from django.http import HttpResponse
# from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.conf import settings
# from django.contrib import messages
# from django.utils import timezone
from rest_framework import viewsets, serializers, views
# from rest_framework.decorators import detail_route, list_route, renderer_classes
from rest_framework.decorators import action as detail_route
from rest_framework.decorators import action as list_route
from rest_framework.decorators import renderer_classes
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
# from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser, BasePermission
# from rest_framework.pagination import PageNumberPagination
# from datetime import datetime, timedelta
# from collections import OrderedDict
from django.core.cache import cache
# from ledger.accounts.models import EmailUser, Address
from ledger_api_client.ledger_models import EmailUserRO as EmailUser
from mooringlicensing.components.main.decorators import basic_exception_handler

# from ledger.address.models import Country
# from datetime import datetime, timedelta, date
from mooringlicensing.components.compliances.models import (
   Compliance,
   ComplianceAmendmentRequest,
   ComplianceAmendmentReason
)
from mooringlicensing.components.compliances.serializers import (
    ComplianceSerializer,
    InternalComplianceSerializer,
    SaveComplianceSerializer,
    ComplianceActionSerializer,
    ComplianceCommsSerializer,
    ComplianceAmendmentRequestSerializer,
    CompAmendmentRequestDisplaySerializer, ListComplianceSerializer
)
from mooringlicensing.components.proposals.models import ProposalApplicant
from mooringlicensing.components.organisations.models import Organisation
from mooringlicensing.helpers import is_customer, is_internal
from rest_framework_datatables.pagination import DatatablesPageNumberPagination

logger = logging.getLogger(__name__)


class ComplianceViewSet(viewsets.ModelViewSet):
    serializer_class = ComplianceSerializer
    queryset = Compliance.objects.none()

    def get_queryset(self):
        if is_internal(self.request):
            return Compliance.objects.all().exclude(processing_status='discarded')
        elif is_customer(self.request):
            # user_orgs = [org.id for org in self.request.user.mooringlicensing_organisations.all()]
            user_orgs = Organisation.objects.filter(delegates__contains=[self.request.user.id])
            # queryset =  Compliance.objects.filter( Q(proposal__org_applicant_id__in = user_orgs) | Q(proposal__submitter = self.request.user) ).exclude(processing_status='discarded')
            queryset =  Compliance.objects.filter(Q(proposal__org_applicant__in=user_orgs) | Q(proposal__submitter=self.request.user.id)).exclude(processing_status='discarded')
            return queryset
        return Compliance.objects.none()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        # Filter by org
        org_id = request.GET.get('org_id',None)
        if org_id:
            queryset = queryset.filter(proposal__org_applicant_id=org_id)
        submitter_id = request.GET.get('submitter_id', None)
        if submitter_id:
            queryset = queryset.filter(proposal__submitter_id=submitter_id)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    def internal_compliance(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = InternalComplianceSerializer(instance,context={'request':request})
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def submit(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()

            # Can only modify if Due or Future.
            if instance.processing_status not in ['due', 'future']:
                raise serializers.ValidationError('The status of this application means it cannot be modified: {}'
                                                    .format(instance.processing_status))

            if instance.proposal.applicant_email != request.user.email:
                raise serializers.ValidationError('You are not authorised to modify this application.')

            data = {
                'text': request.data.get('detail'),
                'num_participants': request.data.get('num_participants')
            }

            serializer = SaveComplianceSerializer(instance, data=data)
            serializer.is_valid(raise_exception=True)
            instance = serializer.save()

            if 'num_participants' in request.data:
                if request.FILES:
                    # if num_adults is present instance.submit is executed after payment in das_payment/views.py
                    for f in request.FILES:
                        document = instance.documents.create(name=str(request.FILES[f]),_file = request.FILES[f])
            else:
                instance.submit(request)

            serializer = self.get_serializer(instance)
            return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def assign_request_user(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.assign_to(request.user,request)
        serializer = InternalComplianceSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def delete_document(self, request, *args, **kwargs):
        instance = self.get_object()
        doc=request.data.get('document')
        instance.delete_document(request, doc)
        serializer = ComplianceSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def assign_to(self, request, *args, **kwargs):
        instance = self.get_object()
        user_id = request.data.get('user_id',None)
        user = None
        if not user_id:
            raise serializers.ValiationError('A user id is required')
        try:
            user = EmailUser.objects.get(id=user_id)
        except EmailUser.DoesNotExist:
            raise serializers.ValidationError('A user with the id passed in does not exist')
        instance.assign_to(user,request)
        serializer = InternalComplianceSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def unassign(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.unassign(request)
        serializer = InternalComplianceSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def accept(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.accept(request)
        serializer = InternalComplianceSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def amendment_request(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.amendment_requests
        qs = qs.filter(status = 'requested')
        serializer = CompAmendmentRequestDisplaySerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def action_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.action_logs.all()
        serializer = ComplianceActionSerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['GET',], detail=True)
    @basic_exception_handler
    def comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.comms_logs.all()
        serializer = ComplianceCommsSerializer(qs,many=True)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    @basic_exception_handler
    def add_comms_log(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = self.get_object()
            mutable=request.data._mutable
            request.data._mutable=True
            request.data['compliance'] = u'{}'.format(instance.id)
            request.data['staff'] = u'{}'.format(request.user.id)
            request.data._mutable=mutable
            serializer = ComplianceCommsSerializer(data=request.data)
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


class ComplianceAmendmentRequestViewSet(viewsets.ModelViewSet):
    queryset = ComplianceAmendmentRequest.objects.all()
    serializer_class = ComplianceAmendmentRequestSerializer

    def get_queryset(self):
        queryset = ComplianceAmendmentRequest.objects.none()
        user = self.request.user
        if is_internal(self.request):
            queryset = ComplianceAmendmentRequest.objects.all()
        elif is_customer(self.request):
            user_orgs = [org.id for org in Organisation.objects.filter(delegates__contains=[self.request.user.id])]
            queryset = ComplianceAmendmentRequest.objects.filter(
                Q(compliance__proposal__org_applicant_id__in=user_orgs) | Q(compliance__proposal__submitter=user.id)
            )
        else:
            logger.warn("User is neither customer nor internal user: {} <{}>".format(user.get_full_name(), user.email))
        return queryset

    @basic_exception_handler
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data= request.data)
        serializer.is_valid(raise_exception = True)
        instance = serializer.save()
        instance.generate_amendment(request)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class ComplianceAmendmentReasonChoicesView(views.APIView):

    renderer_classes = [JSONRenderer,]
    def get(self,request, format=None):
        choices_list = []
        choices=ComplianceAmendmentReason.objects.all()
        if choices:
            for c in choices:
                choices_list.append({'key': c.id,'value': c.reason})
        return Response(choices_list)


class GetComplianceStatusesDict(views.APIView):
    renderer_classes = [JSONRenderer, ]

    def get(self, request, format=None):
        #data = [{'code': i[0], 'description': i[1]} for i in Compliance.CUSTOMER_STATUS_CHOICES]
        #return Response(data)
        data = {}
        if not cache.get('compliance_internal_statuses_dict') or not cache.get('compliance_external_statuses_dict'):
            cache.set('compliance_internal_statuses_dict',[{'code': i[0], 'description': i[1]} for i in Compliance.PROCESSING_STATUS_CHOICES], settings.LOV_CACHE_TIMEOUT)
            cache.set('compliance_external_statuses_dict',[{'code': i[0], 'description': i[1]} for i in Compliance.CUSTOMER_STATUS_CHOICES if i[0] != 'discarded'], settings.LOV_CACHE_TIMEOUT)
        data['external_statuses'] = cache.get('compliance_external_statuses_dict')
        data['internal_statuses'] = cache.get('compliance_internal_statuses_dict')
        return Response(data)


class ComplianceFilterBackend(DatatablesFilterBackend):
    def filter_queryset(self, request, queryset, view):
        total_count = queryset.count()

        filter_compliance_status = request.GET.get('filter_compliance_status')
        if filter_compliance_status and not filter_compliance_status.lower() == 'all':
            queryset = queryset.filter(processing_status=filter_compliance_status)

        fields = self.get_fields(request)
        ordering = self.get_ordering(request, view, fields)
        queryset = queryset.order_by(*ordering)
        if len(ordering):
            queryset = queryset.order_by(*ordering)

        try:
            super_queryset = super(ComplianceFilterBackend, self).filter_queryset(request, queryset, view)

            # Custom search 
            search_text = request.GET.get('search[value]')  # This has a search term.
            if search_text:
                email_user_ids = list(EmailUser.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
                .filter(
                    Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
                ).values_list("id", flat=True))
                proposal_applicant_proposals = list(ProposalApplicant.objects.annotate(full_name=Concat('first_name',Value(" "),'last_name',output_field=CharField()))
                .filter(
                    Q(first_name__icontains=search_text) | Q(last_name__icontains=search_text) | Q(email__icontains=search_text) | Q(full_name__icontains=search_text)
                ).values_list("proposal_id", flat=True))
                q_set = queryset.filter(Q(approval__current_proposal__id__in=proposal_applicant_proposals)|Q(approval__current_proposal__submitter__in=email_user_ids))

                queryset = super_queryset.union(q_set)
            return queryset
        except Exception as e:
            logger.error(f'ComplianceFilterBackend raises an error: [{e}].  Query may not work correctly.')
        setattr(view, '_datatables_total_count', total_count)
        return queryset


class ComplianceRenderer(DatatablesRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        if 'view' in renderer_context and hasattr(renderer_context['view'], '_datatables_total_count'):
            data['recordsTotal'] = renderer_context['view']._datatables_total_count
        return super(ComplianceRenderer, self).render(data, accepted_media_type, renderer_context)


class CompliancePaginatedViewSet(viewsets.ModelViewSet):
    filter_backends = (ComplianceFilterBackend,)
    pagination_class = DatatablesPageNumberPagination
    renderer_classes = (ComplianceRenderer,)
    queryset = Compliance.objects.none()
    serializer_class = ListComplianceSerializer
    search_fields = ['lodgement_number', ]
    page_size = 10

    def get_queryset(self):
        request_user = self.request.user
        qs = Compliance.objects.none()

        target_email_user_id = int(self.request.GET.get('target_email_user_id', 0))

        if is_internal(self.request):
            if target_email_user_id:
                target_user = EmailUser.objects.get(id=target_email_user_id)
                qs = Compliance.objects.filter(Q(approval__submitter=target_user.id))
            else:
                qs = Compliance.objects.all()
        elif is_customer(self.request):
            qs = Compliance.objects.filter(Q(approval__submitter=request_user.id)).exclude(processing_status="discarded")

        return qs

    @list_route(methods=['GET',], detail=False)
    def list_external(self, request, *args, **kwargs):
        """
        User is accessing /external/ page
        """
        qs = self.get_queryset()
        qs = self.filter_queryset(qs)

        self.paginator.page_size = qs.count()
        #result_page = self.paginator.paginate_queryset(qs, request)
        result_page = self.paginator.paginate_queryset(qs, request)
        serializer = ListComplianceSerializer(result_page, context={'request': request}, many=True)
        return self.paginator.get_paginated_response(serializer.data)

