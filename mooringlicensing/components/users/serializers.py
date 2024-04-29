from django.conf import settings
# from ledger.accounts.models import EmailUser,Address, Profile,EmailIdentity, EmailUserAction, EmailUserLogEntry, CommunicationsLogEntry
from ledger_api_client.ledger_models import EmailUserRO, Address
from mooringlicensing.components.main.serializers import CommunicationLogEntrySerializer
from mooringlicensing.components.organisations.models import (
                                    Organisation,
                                )
from mooringlicensing.components.main.models import UserSystemSettings, Document#, ApplicationType
from mooringlicensing.components.proposals.models import Proposal, ProposalApplicant
from mooringlicensing.components.organisations.utils import can_admin_org, is_consultant
from rest_framework import serializers

from mooringlicensing.components.users.models import EmailUserLogEntry
# from ledger.accounts.utils import in_dbca_domain
from mooringlicensing.helpers import is_mooringlicensing_admin, in_dbca_domain
# from ledger.payments.helpers import is_payment_admin
from ledger_api_client.helpers import is_payment_admin

class DocumentSerializer(serializers.ModelSerializer):

    class Meta:
        model = Document
        fields = ('id','description','file','name','uploaded_date')

class UserAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = (
            'id',
            'line1',
            'locality',
            'state',
            'country',
            'postcode'
        )

class UserSystemSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSystemSettings
        fields = (
            'one_row_per_park',
        )

class UserOrganisationSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='organisation.name')
    abn = serializers.CharField(source='organisation.abn')
    email = serializers.SerializerMethodField()
    is_consultant = serializers.SerializerMethodField(read_only=True)
    is_admin = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Organisation
        fields = (
            'id',
            'name',
            'abn',
            'email',
            'is_consultant',
            'is_admin',
        )

    def get_is_admin(self, obj):
        user = EmailUserRO.objects.get(id=self.context.get('user_id'))
        return can_admin_org(obj, user)

    def get_is_consultant(self, obj):
        user = EmailUserRO.objects.get(id=self.context.get('user_id'))
        return is_consultant(obj, user)

    def get_email(self, obj):
        email = EmailUserRO.objects.get(id=self.context.get('user_id')).email
        return email

    
class UserForEndorserSerializer(serializers.ModelSerializer):

    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'email',
            'phone_number',
        )


class UserFilterSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'email',
            'name'
        )

    def get_name(self, obj):
        return obj.get_full_name()


class ProposalApplicantForEndorserSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProposalApplicant
        fields = (
            'id',
            'last_name',
            'first_name',
            'email',
            'phone_number',
        )

class EmailUserRoForEndorserSerializer(serializers.ModelSerializer):

    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'email',
            'phone_number',
        )


class EmailUserRoSerializer(serializers.ModelSerializer):
    residential_line1 = serializers.CharField(source='residential_address.line1')
    residential_line2 = serializers.CharField(source='residential_address.line2')
    residential_line3 = serializers.CharField(source='residential_address.line3')
    residential_locality = serializers.CharField(source='residential_address.locality')
    residential_state = serializers.CharField(source='residential_address.state')
    residential_country = serializers.CharField(source='residential_address.country')
    residential_postcode = serializers.CharField(source='residential_address.postcode')
    postal_line1 = serializers.CharField(source='postal_address.line1')
    postal_line2 = serializers.CharField(source='postal_address.line2')
    postal_line3 = serializers.CharField(source='postal_address.line3')
    postal_locality = serializers.CharField(source='postal_address.locality')
    postal_state = serializers.CharField(source='postal_address.state')
    postal_country = serializers.CharField(source='postal_address.country')
    postal_postcode = serializers.CharField(source='postal_address.postcode')

    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'dob',

            'residential_line1',
            'residential_line2',
            'residential_line3',
            'residential_locality',
            'residential_state',
            'residential_country',
            'residential_postcode',

            'postal_same_as_residential',

            'postal_line1',
            'postal_line2',
            'postal_line3',
            'postal_locality',
            'postal_state',
            'postal_country',
            'postal_postcode',

            'email',
            'phone_number',
            'mobile_number',
        )
        
class ProposalApplicantSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    ledger_id = serializers.SerializerMethodField()

    class Meta:
        model = ProposalApplicant
        fields = (
            'id',
            'last_name',
            'first_name',
            'full_name',
            'dob',

            'residential_line1',
            'residential_line2',
            'residential_line3',
            'residential_locality',
            'residential_state',
            'residential_country',
            'residential_postcode',

            'postal_same_as_residential',
            'postal_line1',
            'postal_line2',
            'postal_line3',
            'postal_locality',
            'postal_state',
            'postal_country',
            'postal_postcode',

            'email',
            'phone_number',
            'mobile_number',

            'ledger_id',
        )
    
    def get_full_name(self, obj):
        return obj.get_full_name()

    def get_ledger_id(self, obj):
        try:
            return obj.proposal.submitter
        except:
            return


class UserSerializer(serializers.ModelSerializer):
    residential_address = UserAddressSerializer()
    postal_address = serializers.SerializerMethodField()
    personal_details = serializers.SerializerMethodField()
    address_details = serializers.SerializerMethodField()
    contact_details = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    first_name = serializers.SerializerMethodField()
    last_name = serializers.SerializerMethodField()
    is_department_user = serializers.SerializerMethodField()
    is_payment_admin = serializers.SerializerMethodField()
    system_settings= serializers.SerializerMethodField()
    is_payment_admin = serializers.SerializerMethodField()
    is_mooringlicensing_admin = serializers.SerializerMethodField()
    readonly_first_name = serializers.SerializerMethodField()
    readonly_last_name = serializers.SerializerMethodField()
    readonly_email = serializers.SerializerMethodField()
    readonly_dob = serializers.SerializerMethodField()

    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'email',
            'residential_address',
            'postal_address',
            'phone_number',
            'mobile_number',
            'personal_details',
            'address_details',
            'contact_details',
            'full_name',
            'is_department_user',
            'is_payment_admin',
            'is_staff',
            'system_settings',
            'is_mooringlicensing_admin',
            'postal_same_as_residential',
            'readonly_first_name',
            'readonly_last_name',
            'readonly_email',
            'dob',
            'readonly_dob',
        )

    def get_readonly_dob(self, obj):
        return True if obj.dob else False

    def get_readonly_first_name(self, obj):
        return True if obj.first_name else False

    def get_readonly_last_name(self, obj):
        return True if obj.last_name else False

    def get_readonly_email(self, obj):
        return True if obj.email else False

    def get_postal_address(self, obj):
        address = {}
        if obj.postal_address:
            address = UserAddressSerializer(obj.postal_address).data
        return address

    def get_personal_details(self,obj):
        return True if obj.last_name  and obj.first_name else False

    def get_address_details(self,obj):
        #return True if obj.residential_address else False
        return True

    def get_contact_details(self,obj):
        if obj.mobile_number and obj.email:
            return True
        elif obj.phone_number and obj.email:
            return True
        elif obj.mobile_number and obj.phone_number:
            return True
        else:
            return False

    def get_first_name(self, obj):
        first_name = obj.first_name
        if not first_name:
            try:
                return Proposal.objects.filter(submitter=obj.id).order_by("lodgement_date").last().proposal_applicant.first_name
            except Exception as e:
                print(e)
                return first_name
        return first_name
    
    def get_last_name(self, obj):
        last_name = obj.last_name
        if not last_name:
            try:
                return Proposal.objects.filter(submitter=obj.id).order_by("lodgement_date").last().proposal_applicant.last_name
            except:
                return last_name
        return last_name

    def get_full_name(self, obj):
        first_name = obj.first_name
        last_name = obj.last_name
        full_name = obj.get_full_name()
        if not first_name or not last_name:
            try:
                return Proposal.objects.filter(submitter=obj.id).order_by("lodgement_date").last().proposal_applicant.get_full_name()
            except:
                return full_name
        return full_name

    def get_is_department_user(self, obj):
        # if obj.email:
        #     return in_dbca_domain(obj)
        # else:
        #     return False
        if obj.email:
            request = self.context["request"] if self.context else None
            if request:
                return in_dbca_domain(request)
        return False

    def get_is_payment_admin(self, obj):
        return is_payment_admin(obj)

    def get_system_settings(self, obj):
        try:
            user_system_settings = obj.system_settings.first()
            serialized_settings = UserSystemSettingsSerializer(
                user_system_settings).data
            return serialized_settings
        except:
            return None

    def get_is_mooringlicensing_admin(self, obj):
        request = self.context['request'] if self.context else None
        if request:
            return is_mooringlicensing_admin(request)
        return False


class PersonalSerializer(serializers.ModelSerializer):
    dob = serializers.DateField(format="%d/%m/%Y",input_formats=['%d/%m/%Y'],required=False,allow_null=True)
    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'last_name',
            'first_name',
            'dob',
        )

class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailUserRO
        fields = (
            'id',
            'email',
            'phone_number',
            'mobile_number',
        )

    def validate(self, obj):
        #Mobile and phone number for dbca user are updated from active directory so need to skip these users from validation.
        domain=None
        if obj['email']:
            domain = obj['email'].split('@')[1]
        if domain in settings.DEPT_DOMAINS:
            return obj
        else:
            if not obj.get('phone_number') and not obj.get('mobile_number'):
                raise serializers.ValidationError('You must provide a mobile/phone number')
        return obj

# class EmailUserActionSerializer(serializers.ModelSerializer):
#     who = serializers.CharField(source='who.get_full_name')
#
#     class Meta:
#         model = EmailUserAction
#         fields = '__all__'

# class EmailUserCommsSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = EmailUserLogEntry
#         fields = '__all__'


class EmailUserCommsSerializer(CommunicationLogEntrySerializer):
    # TODO: implement
    documents = serializers.SerializerMethodField()
    # type = serializers.CharField(source='log_type')
    #
    class Meta:
        model = EmailUserLogEntry
    #     # fields = '__all__'
        fields = (
            'id',
            'customer',
            'to',
            'fromm',
            'cc',
            'type',
            'reference',
            'subject',
            'text',
            'created',
            'staff',
            # 'emailuser',
            'email_user_id',
            'documents',
        )
        read_only_fields = (
            'customer',
        )


class CommunicationLogEntrySerializer(serializers.ModelSerializer):
    # TODO: implement
    pass
#     customer = serializers.PrimaryKeyRelatedField(queryset=EmailUser.objects.all(),required=False)
#     documents = serializers.SerializerMethodField()
#     class Meta:
#         model = CommunicationsLogEntry
#         fields = (
#             'id',
#             'customer',
#             'to',
#             'fromm',
#             'cc',
#             'log_type',
#             'reference',
#             'subject'
#             'text',
#             'created',
#             'staff',
#             'emailuser',
#             'documents'
#         )
#
#     def get_documents(self,obj):
#         return [[d.name,d._file.url] for d in obj.documents.all()]


class EmailUserLogEntrySerializer(CommunicationLogEntrySerializer):
    # TODO: implement
#     documents = serializers.SerializerMethodField()
    class Meta:
        model = EmailUserLogEntry
        fields = '__all__'
#         read_only_fields = (
#             'customer',
#         )
#
#     def get_documents(self,obj):
#         return [[d.name,d._file.url] for d in obj.documents.all()]
