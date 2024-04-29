from __future__ import unicode_literals
# from ledger.accounts.models import EmailUser
from django.conf import settings
from django.core.cache import cache

import logging
import ledger_api_client

from rest_framework import serializers

from mooringlicensing.components.proposals.models import Proposal

logger = logging.getLogger(__name__)

def belongs_to(user, group_name):
    """
    Check if the user belongs to the given group.
    :param user:
    :param group_name:
    :return:
    """
    belongs_to_value = cache.get(
        "User-belongs_to" + str(user.id) + "group_name:" + group_name
    )
    # if belongs_to_value:
    #     logger.info(f'From Cache - User-belongs_to: {str(user.id)}, group_name: {group_name}')
    if belongs_to_value is None:
    #     belongs_to_value = user.groups().filter(name=group_name).exists()
        belongs_to_value = False
        system_group = ledger_api_client.managed_models.SystemGroup.objects.get(name=group_name)
        if user.id in system_group.get_system_group_member_ids():
            belongs_to_value = True
        cache.set(
            "User-belongs_to" + str(user.id) + "group_name:" + group_name,
            belongs_to_value,
            3600,
        )
    return belongs_to_value

def is_model_backend(request):
    # Return True if user logged in via single sign-on (i.e. an internal)
    return 'ModelBackend' in request.session.get('_auth_user_backend')

def is_email_auth_backend(request):
    # Return True if user logged in via social_auth (i.e. an external user signing in with a login-token)
    return 'EmailAuth' in request.session.get('_auth_user_backend')

def is_mooringlicensing_admin(request):
    # return request.user.is_authenticated() and is_model_backend(request) and in_dbca_domain(request) and (belongs_to(request.user, settings.ADMIN_GROUP))
    return request.user.is_authenticated and is_model_backend(request) and in_dbca_domain(request) and (belongs_to(request.user, settings.ADMIN_GROUP))

def in_dbca_domain(request):
    return request.user.is_staff
    # user = request.user
    # domain = user.email.split('@')[1]
    # if domain in settings.DEPT_DOMAINS:
    #     if not user.is_staff:
    #         # hack to reset department user to is_staff==True, if the user logged in externally (external departmentUser login defaults to is_staff=False)
    #         user.is_staff = True
    #         user.save()
    #     return True
    # return False

def is_in_organisation_contacts(request, organisation):
    return request.user.email in organisation.contacts.all().values_list('email', flat=True)

def is_departmentUser(request):
    # return request.user.is_authenticated() and is_model_backend(request) and in_dbca_domain(request)
    return request.user.is_authenticated and is_model_backend(request) and in_dbca_domain(request)

def is_customer(request):
    # return request.user.is_authenticated() and (is_model_backend(request) or is_email_auth_backend(request))
    return request.user.is_authenticated and (is_model_backend(request) or is_email_auth_backend(request))

def is_internal(request):
    return is_departmentUser(request)

def is_authorised_to_modify(request, instance):
    authorised = True

    if is_customer(request):
        # the status of the application must be DRAFT for customer to modify
        authorised &= instance.processing_status in [Proposal.PROCESSING_STATUS_DRAFT, Proposal.PROCESSING_STATUS_AWAITING_DOCUMENTS, Proposal.PROCESSING_STATUS_PRINTING_STICKER,]
        # the applicant and submitter must be the same
        authorised &= request.user.email == instance.applicant_email

    if not authorised:
        logger.warning(f'User: [{request.user}] is not authorised to modify this proposal: [{instance}].  Raise an error.')
        raise serializers.ValidationError('You are not authorised to modify this application.')


    # authorised = True

    # if is_internal(request):
    #     # the status must be 'with_assessor'
    #     authorised &= instance.processing_status == 'with_assessor'
    #     # the user must be an assessor for this type of application
    #     authorised &= instance.can_process()
    # elif is_customer(request):
    #     # the status of the application must be DRAFT for customer to modify
    #     authorised &= instance.processing_status == 'draft'

    #     applicantType = instance.applicant_type
    #     # Applicant is individual
    #     if applicantType == 'SUB':
    #         authorised &= instance.submitter != request.user.email
    #     # the application org and submitter org must be the same
    #     else:
    #         authorised &= is_in_organisation_contacts(request, instance.org_applicant)

    # if not authorised:
    #     raise serializers.ValidationError('You are not authorised to modify this application.')

def is_applicant_address_set(instance):

    applicant = instance.proposal_applicant

    #residential address
    if not applicant.residential_line1 or \
        not applicant.residential_locality or \
        not applicant.residential_state or \
        not applicant.residential_country or \
        not applicant.residential_postcode:
        raise serializers.ValidationError('Residential Address details not provided')

    #postal same as residential OR postal address
    if not applicant.postal_same_as_residential and \
        (not applicant.postal_line1 or \
        not applicant.postal_locality or \
        not applicant.postal_state or \
        not applicant.postal_country or \
        not applicant.postal_postcode):
        raise serializers.ValidationError('Postal Address details not provided')