import traceback
# import base64
# import geojson
# from six.moves.urllib.parse import urlparse
# from wsgiref.util import FileWrapper
from django.db.models import Q, Min, CharField, Value
from django.db.models.functions import Concat
from django.db import transaction
# from django.http import HttpResponse
# from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.conf import settings
# from django.contrib import messages
# from django.views.decorators.http import require_http_methods
# from django.views.decorators.csrf import csrf_exempt
# from django.utils import timezone
from django_countries import countries
from rest_framework import viewsets, serializers, status, generics, views
# from rest_framework.decorators import detail_route, list_route,renderer_classes
from rest_framework.decorators import action as detail_route
# from rest_framework.decorators import action as list_route
from rest_framework.decorators import renderer_classes
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework.permissions import BasePermission, OR
# from rest_framework.pagination import PageNumberPagination
# from datetime import datetime, timedelta
# from collections import OrderedDict
from django.core.cache import cache
# from ledger.accounts.models import EmailUser,Address, Profile, EmailIdentity, EmailUserAction
from ledger_api_client.ledger_models import EmailUserRO as EmailUser, Address
from mooringlicensing.components.approvals.models import Approval
from mooringlicensing.components.proposals.models import ProposalApplicant
from django.core.paginator import Paginator, EmptyPage
from mooringlicensing.components.main.decorators import basic_exception_handler
# from ledger.address.models import Country
# from datetime import datetime,timedelta, date
# from mooringlicensing.components.main.decorators import (
#         basic_exception_handler,
#         timeit,
#         query_debugger
#         )
# from mooringlicensing.components.organisations.models import  (
#                                     Organisation,
#                                 )
from mooringlicensing.components.proposals.serializers import EmailUserAppViewSerializer
from mooringlicensing.components.users.models import EmailUserLogEntry
from mooringlicensing.components.users.serializers import (
    EmailUserRoForEndorserSerializer,
    EmailUserRoSerializer,
    ProposalApplicantForEndorserSerializer,
    UserForEndorserSerializer,
    UserSerializer,
    UserFilterSerializer,
    UserAddressSerializer,
    PersonalSerializer,
    ContactSerializer,
    # EmailUserActionSerializer,
    EmailUserCommsSerializer,
    EmailUserLogEntrySerializer,
    UserSystemSettingsSerializer, ProposalApplicantSerializer,
)
from mooringlicensing.components.organisations.serializers import (
    OrganisationRequestDTSerializer,
)
from mooringlicensing.components.main.models import UserSystemSettings
# from mooringlicensing.components.main.process_document import (
#         process_generic_document,
#         )

import logging

from mooringlicensing.helpers import is_customer, is_internal
from mooringlicensing.ledger_api_utils import retrieve_email_userro
# logger = logging.getLogger('mooringlicensing')
logger = logging.getLogger(__name__)


class GetCountries(views.APIView):
    renderer_classes = [JSONRenderer,]
    def get(self, request, format=None):
        data = cache.get('country_list')
        if not data:
            country_list = []
            for country in list(countries):
                country_list.append({"name": country.name, "code": country.code})
            cache.set('country_list',country_list, settings.LOV_CACHE_TIMEOUT)
            data = cache.get('country_list')
        return Response(data)


class GetProposalApplicant(views.APIView):
    renderer_classes = [JSONRenderer,]

    def get(self, request, proposal_pk, format=None):
        from mooringlicensing.components.proposals.models import Proposal, ProposalApplicant
        proposal = Proposal.objects.get(id=proposal_pk)
        if (is_customer(self.request) and proposal.submitter == request.user.id) or is_internal(self.request):
            # Holder of this proposal is accessing OR internal user is accessing.
            if proposal.proposal_applicant:
                # When proposal has a proposal_applicant
                serializer = ProposalApplicantSerializer(proposal.proposal_applicant, context={'request': request})
            else:
                submitter = retrieve_email_userro(proposal.submitter)
                serializer = EmailUserRoSerializer(submitter)
            return Response(serializer.data)
        elif is_customer(self.request) and proposal.site_licensee_email == request.user.email:
            # ML holder is accessing the proposal as an endorser
            if proposal.proposal_applicant:
                # When proposal has a proposal_applicant
                serializer = ProposalApplicantForEndorserSerializer(proposal.proposal_applicant, context={'request': request})
            else:
                submitter = retrieve_email_userro(proposal.submitter)
                serializer = EmailUserRoForEndorserSerializer(submitter)
            return Response(serializer.data)


class GetProfile(views.APIView):
    renderer_classes = [JSONRenderer,]

    def get(self, request, format=None):
        serializer = UserSerializer(request.user, context={'request':request})
        response = Response(serializer.data)
        return response


class GetPerson(views.APIView):
    renderer_classes = [JSONRenderer,]

    def get(self, request, format=None):
        search_term = request.GET.get('search_term', '')
        page_number = request.GET.get('page_number', 1)
        items_per_page = 10

        if search_term:
            my_queryset = ProposalApplicant.objects.annotate(
                custom_term=Concat(
                    "first_name",
                    Value(" "),
                    "last_name",
                    Value(" "),
                    "email",
                    output_field=CharField(),
                    )
                ).distinct("custom_term").order_by("custom_term").filter(custom_term__icontains=search_term)
            paginator = Paginator(my_queryset, items_per_page)
            try:
                current_page = paginator.page(page_number)
                my_objects = current_page.object_list
            except EmptyPage:
                my_objects = []

            data_transform = []
            for proposal_applicant in my_objects:
                if proposal_applicant.dob:
                    text = '{} {} (DOB: {})'.format(proposal_applicant.first_name, proposal_applicant.last_name, proposal_applicant.dob)
                else:
                    text = '{} {}'.format(proposal_applicant.first_name, proposal_applicant.last_name)

                serializer = ProposalApplicantSerializer(proposal_applicant)
                proposal_applicant_data = serializer.data
                proposal_applicant_data['text'] = text
                data_transform.append(proposal_applicant_data)
            return Response({
                "results": data_transform,
                "pagination": {
                    "more": current_page.has_next()
                }
            })
        return Response()


class GetSubmitterProfile(views.APIView):
    renderer_classes = [JSONRenderer,]
    def get(self, request, format=None):
        submitter_id = request.GET.get('submitter_id')
        submitter = EmailUser.objects.get(id=submitter_id)
        serializer  = UserSerializer(submitter, context={'request':request})
        response = Response(serializer.data)
        return response

from rest_framework import filters
class UserListFilterView(generics.ListAPIView):
    """ https://cop-internal.dbca.wa.gov.au/api/filtered_users?search=russell
    """
    queryset = EmailUser.objects.all()
    serializer_class = UserFilterSerializer
    filter_backends = (filters.SearchFilter,)
    search_fields = ('email', 'first_name', 'last_name')


# class IsOwner(BasePermission):
#     def has_object_permission(self, request, view, obj):
#         return obj == request.user


# class IsInternalUser(BasePermission):
#     def has_object_permission(self, request, view, obj):
#         return is_internal(request)


class UserViewSet(viewsets.ModelViewSet):
    queryset = EmailUser.objects.none()
    serializer_class = UserSerializer
    # permission_classes = [IsOwner | IsInternalUser]

    def get_queryset(self):
        if is_internal(self.request):
            return EmailUser.objects.all()
        elif is_customer(self.request):
            user = self.request.user
            return EmailUser.objects.filter(Q(id=user.id))
        return EmailUser.objects.none()

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_personal(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = PersonalSerializer(instance,data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        serializer = UserSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_contact(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ContactSerializer(instance,data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        serializer = UserSerializer(instance)
        return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    @basic_exception_handler
    def update_address(self, request, *args, **kwargs):
        with transaction.atomic():
            print(request.data)
            instance = self.get_object()
            # residential address
            residential_serializer = UserAddressSerializer(data=request.data.get('residential_address'))
            residential_serializer.is_valid(raise_exception=True)
            residential_address, created = Address.objects.get_or_create(
                line1 = residential_serializer.validated_data['line1'],
                locality = residential_serializer.validated_data['locality'],
                state = residential_serializer.validated_data['state'],
                country = residential_serializer.validated_data['country'],
                postcode = residential_serializer.validated_data['postcode'],
                user = instance
            )
            instance.residential_address = residential_address
            # postal address
            postal_address_data = request.data.get('postal_address')
            postal_address = None
            if request.data.get('postal_same_as_residential'):
                instance.postal_same_as_residential = True
                instance.postal_address = residential_address
            elif postal_address_data and postal_address_data.get('line1'):
                postal_serializer = UserAddressSerializer(data=postal_address_data)
                postal_serializer.is_valid(raise_exception=True)
                postal_address, created = Address.objects.get_or_create(
                    line1 = postal_serializer.validated_data['line1'],
                    locality = postal_serializer.validated_data['locality'],
                    state = postal_serializer.validated_data['state'],
                    country = postal_serializer.validated_data['country'],
                    postcode = postal_serializer.validated_data['postcode'],
                    user = instance
                )
                instance.postal_address = postal_address
                instance.postal_same_as_residential = False
            else:
                instance.postal_same_as_residential = False
            instance.save()

            # Postal address form must be completed or checkbox ticked
            if not postal_address and not instance.postal_same_as_residential:
                raise serializers.ValidationError("Postal address not provided")

            serializer = UserSerializer(instance)
            return Response(serializer.data)

    @detail_route(methods=['POST',], detail=True)
    def update_system_settings(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            user_setting, created = UserSystemSettings.objects.get_or_create(
                user = instance
            )
            serializer = UserSystemSettingsSerializer(user_setting, data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            instance = self.get_object()
            serializer = UserSerializer(instance)
            return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @detail_route(methods=['POST',], detail=True)
    def upload_id(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.upload_identification(request)
            with transaction.atomic():
                instance.save()

                # TODO: log user action
                # instance.log_user_action(EmailUserAction.ACTION_ID_UPDATE.format(
                # '{} {} ({})'.format(instance.first_name, instance.last_name, instance.email)), request)

            serializer = UserSerializer(instance, partial=True)
            return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @detail_route(methods=['GET', ], detail=True)
    def pending_org_requests(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = OrganisationRequestDTSerializer(
                instance.organisationrequest_set.filter(
                    status='with_assessor'),
                many=True,
                context={'request': request})
            return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @detail_route(methods=['GET', ], detail=True)
    def action_log(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            qs = instance.action_logs.all()

            # TODO: return a list of user actions
            # serializer = EmailUserActionSerializer(qs, many=True)
            # return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @detail_route(methods=['GET',], detail=True)
    def comms_log(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            # qs = instance.comms_logs.all()
            qs = EmailUserLogEntry.objects.filter(email_user_id=instance.id)
            # qs = EmailUserLogEntry.objects.filter(email_user_id=132848)
            serializer = EmailUserCommsSerializer(qs, many=True)
            return Response(serializer.data)
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

    @detail_route(methods=['POST',], detail=True)
    @renderer_classes((JSONRenderer,))
    def add_comms_log(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                instance = self.get_object()
                # mutable=request.data._mutable
                # request.data._mutable=True
                # request.data['emailuser'] = u'{}'.format(instance.id)
                # request.data['staff'] = u'{}'.format(request.user.id)
                # request.data._mutable=mutable
                # serializer = EmailUserLogEntrySerializer(data=request.data)
                # serializer.is_valid(raise_exception=True)
                # comms = serializer.save()
                ### Save the files
                # for f in request.FILES:
                #     document = comms.documents.create()
                #     document.name = str(request.FILES[f])
                #     document._file = request.FILES[f]
                #     document.save()
                ### End Save Documents
                kwargs = {
                    'subject': request.data.get('subject', ''),
                    'text': request.data.get('text', ''),
                    'email_user_id': instance.id,
                    'customer': instance.id,
                    'staff': request.data.get('staff', request.user.id),
                    'to': request.data.get('to', ''),
                    'fromm': request.data.get('fromm', ''),
                    'cc': '',
                }
                eu_entry = EmailUserLogEntry.objects.create(**kwargs)

                # for attachment in attachments:
                for f in request.FILES:
                    document = eu_entry.documents.create(
                    name = str(request.FILES[f]),
                    _file = request.FILES[f]
                    )
                return Response({})
        except serializers.ValidationError:
            print(traceback.print_exc())
            raise
        except ValidationError as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(repr(e.error_dict))
        except Exception as e:
            print(traceback.print_exc())
            raise serializers.ValidationError(str(e))

