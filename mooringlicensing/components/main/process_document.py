import logging

#from django.core.files.storage import default_storage
import os
from django.core.files.base import ContentFile
import traceback
from django.conf import settings
from django.core.files.storage import FileSystemStorage

from mooringlicensing.components.proposals.models import (
    ProposalMooringReportDocument, ProposalWrittenProofDocument, ProposalSignedLicenceAgreementDocument,
    ProposalProofOfIdentityDocument
)
from mooringlicensing.settings import PRIVATE_MEDIA_BASE_URL, PRIVATE_MEDIA_STORAGE_LOCATION

logger = logging.getLogger(__name__)

# private_storage = FileSystemStorage(  # We want to store files in secure place (outside of the media folder)
#     location=PRIVATE_MEDIA_STORAGE_LOCATION,
#     base_url=PRIVATE_MEDIA_BASE_URL,
# )
from mooringlicensing.components.proposals.models import private_storage

def process_generic_document(request, instance, document_type=None, *args, **kwargs):
    logger.info(f'Processing document... Data: [{request.data}] ...')
    try:
        action = request.data.get('action')

        input_name = request.data.get('input_name')
        comms_log_id = request.data.get('comms_log_id')
        comms_instance = None

        if document_type == 'comms_log' and comms_log_id and comms_log_id != 'null':
            comms_instance = instance.comms_logs.get(id=comms_log_id)
        elif document_type == 'comms_log':
            comms_instance = instance.comms_logs.create()

        if action == 'list':
            # Retrieve list later of this function
            pass
        elif action == 'delete':
            delete_document(request, instance, comms_instance, document_type, input_name)
        elif action == 'cancel':
            deleted = cancel_document(request, instance, comms_instance, document_type, input_name)
        elif action == 'save':
            save_document(request, instance, comms_instance, document_type, input_name)

        # HTTP Response varies by action and instance type
        ret = None
        if comms_instance and action == 'cancel' and deleted:
            ret = deleted
        elif comms_instance:
            returned_file_data = [dict(file=d._file.url, id=d.id, name=d.name,) for d in comms_instance.documents.all() if d._file]
            ret = {'filedata': returned_file_data, 'comms_instance_id': comms_instance.id}
        # example document_type
        elif input_name:
            if document_type == 'electoral_roll_document':
                documents_qs = instance.electoral_roll_documents
            elif document_type == 'vessel_registration_document':
                documents_qs = instance.vessel_registration_documents
            elif document_type == 'insurance_certificate_document':
                documents_qs = instance.insurance_certificate_documents
            elif document_type == 'hull_identification_number_document':
                documents_qs = instance.hull_identification_number_documents
            elif document_type == 'waiting_list_offer_document':
                documents_qs = instance.waiting_list_offer_documents
            # Mooring Licence
            elif document_type == 'signed_licence_agreement_document':
                documents_qs = instance.signed_licence_agreement_documents.filter(proposalsignedlicenceagreementdocument__enabled=True)
            elif document_type == 'mooring_report_document':
                documents_qs = instance.mooring_report_documents.filter(proposalmooringreportdocument__enabled=True)
            elif document_type == 'written_proof_document':
                documents_qs = instance.written_proof_documents.filter(proposalwrittenproofdocument__enabled=True)
            elif document_type == 'proof_of_identity_document':
                documents_qs = instance.proof_of_identity_documents.filter(proposalproofofidentitydocument__enabled=True)

            returned_file_data = [dict(file=d._file.url, id=d.id, name=d.name,) for d in documents_qs.filter(input_name=input_name) if d._file]
            ret = {'filedata': returned_file_data }
        else:
            returned_file_data = [dict(file=d._file.url, id=d.id, name=d.name, ) for d in instance.documents.all() if d._file]
            ret = {'filedata': returned_file_data}

        return ret

    except Exception as e:
        logger.error(e)
        print(traceback.print_exc())
        raise e


def delete_document(request, instance, comms_instance, document_type, input_name=None):
    # example document_type
    if 'document_id' in request.data:
        if document_type == 'electoral_roll_document':
            document_id = request.data.get('document_id')
            document = instance.electoral_roll_documents.get(id=document_id)
        elif document_type == 'vessel_registration_document':
            document_id = request.data.get('document_id')
            document = instance.vessel_registration_documents.get(id=document_id)
        elif document_type == 'insurance_certificate_document':
            document_id = request.data.get('document_id')
            document = instance.insurance_certificate_documents.get(id=document_id)
        elif document_type == 'hull_identification_number_document':
            document_id = request.data.get('document_id')
            document = instance.hull_identification_number_documents.get(id=document_id)
        elif document_type == 'mooring_report_document':
            document_id = request.data.get('document_id')
            document = instance.mooring_report_documents.get(id=document_id)
            link_item = ProposalMooringReportDocument.objects.get(proposal=instance, mooring_report_document=document)
            link_item.enabled = False
            link_item.save()
            return
        elif document_type == 'written_proof_document':
            document_id = request.data.get('document_id')
            document = instance.written_proof_documents.get(id=document_id)
            link_item = ProposalWrittenProofDocument.objects.get(proposal=instance, written_proof_document=document)
            link_item.enabled = False
            link_item.save()
            return
        elif document_type == 'signed_licence_agreement_document':
            document_id = request.data.get('document_id')
            document = instance.signed_licence_agreement_documents.get(id=document_id)
            link_item = ProposalSignedLicenceAgreementDocument.objects.get(proposal=instance, signed_licence_agreement_document=document)
            link_item.enabled = False
            link_item.save()
            return
        elif document_type == 'proof_of_identity_document':
            document_id = request.data.get('document_id')
            document = instance.proof_of_identity_documents.get(id=document_id)
            link_item = ProposalProofOfIdentityDocument.objects.get(proposal=instance, proof_of_identity_document=document)
            link_item.enabled = False
            link_item.save()
            return
        elif document_type == 'waiting_list_offer_document':
            document_id = request.data.get('document_id')
            document = instance.waiting_list_offer_documents.get(id=document_id)
        elif document_type == 'temp_document':
            document_id = request.data.get('document_id')
            document = instance.documents.get(id=document_id)

    # comms_log doc store delete
    elif comms_instance and 'document_id' in request.data:
        document_id = request.data.get('document_id')
        document = comms_instance.documents.get(id=document_id)

    # default doc store delete
    elif 'document_id' in request.data:
        document_id = request.data.get('document_id')
        document = instance.documents.get(id=document_id)

    if document._file and os.path.isfile(
            document._file.path):
        document_file_path = document._file.path
        os.remove(document_file_path)
        logger.info(f'Document: [{document_file_path}] has been removed from the filesystem.')

    if document:
        document.delete()
        logger.info(f'Document: [{document}] has been removed from the application: [{instance}].')


def cancel_document(request, instance, comms_instance, document_type, input_name=None):
        if document_type in [
                'electoral_roll_document', 
                'vessel_registration_document',
                'insurance_certificate_document',
                'hull_identification_number_document',
                'mooring_report_document',
                'written_proof_document',
                'signed_licence_agreement_document',
                'proof_of_identity_document',
                'waiting_list_offer_document',
                ]:
            document_id = request.data.get('document_id')
        if comms_instance:
            document_list = comms_instance.documents.all()
        else:
            document_list = instance.documents.all()

        for document in document_list:
            if document._file and os.path.isfile(document._file.path):
                os.remove(document._file.path)
            document.delete()

        if comms_instance:
            return comms_instance.delete()


def save_document(request, instance, comms_instance, document_type, input_name=None):
    # example document_type
    document = None
    path = ''

    if 'filename' in request.data and input_name:
        filename = request.data.get('filename')
        _file = request.data.get('_file')

        # if document_type == 'electoral_roll_document':
        #     document = instance.electoral_roll_documents.get_or_create(input_name=input_name, name=filename)[0]
        #     path_format_string = '{}/proposals/{}/electoral_roll_documents/{}'
        # elif document_type == 'vessel_registration_document':
        #     document = instance.vessel_registration_documents.get_or_create(input_name=input_name, name=filename)[0]
        #     path_format_string = '{}/proposals/{}/vessel_registration_documents/{}'
        # elif document_type == 'insurance_certificate_document':
        #     document = instance.insurance_certificate_documents.get_or_create(input_name=input_name, name=filename)[0]
        #     path_format_string = '{}/proposals/{}/insurance_certificate_documents/{}'
        # elif document_type == 'hull_identification_number_document':
        #     document = instance.hull_identification_number_documents.get_or_create(input_name=input_name, name=filename)[0]
        #     path_format_string = '{}/proposals/{}/hull_identification_number_documents/{}'
        # elif document_type == 'mooring_report_document':
        if document_type == 'mooring_report_document':
            document = instance.mooring_report_documents.get_or_create(input_name=input_name, name=filename)[0]
            # path_format_string = '{}/proposals/{}/mooring_report_documents/{}'
            path_format_string = 'proposal/{}/mooring_report_documents/{}'
        elif document_type == 'written_proof_document':
            document = instance.written_proof_documents.get_or_create(input_name=input_name, name=filename)[0]
            # path_format_string = '{}/proposals/{}/written_proof_documents/{}'
            path_format_string = 'proposal/{}/written_proof_documents/{}'
        elif document_type == 'signed_licence_agreement_document':
            document = instance.signed_licence_agreement_documents.get_or_create(input_name=input_name, name=filename)[0]
            # path_format_string = '{}/proposals/{}/signed_licence_agreement_documents/{}'
            path_format_string = 'proposal/{}/signed_licence_agreement_documents/{}'
        elif document_type == 'proof_of_identity_document':
            document = instance.proof_of_identity_documents.get_or_create(input_name=input_name, name=filename)[0]
            # path_format_string = '{}/proposals/{}/proof_of_identity_documents/{}'
            path_format_string = 'proposal/{}/proof_of_identity_documents/{}'
        elif document_type == 'waiting_list_offer_document':
            document = instance.waiting_list_offer_documents.get_or_create(input_name=input_name, name=filename)[0]
            # path_format_string = '{}/approvals/{}/waiting_list_offer_documents/{}'
            path_format_string = 'approval/{}/waiting_list_offer_documents/{}'
        # path = default_storage.save(path_format_string.format(settings.MEDIA_APP_DIR, instance.id, filename), ContentFile(_file.read()))
        # path = private_storage.save(path_format_string.format(settings.MEDIA_APP_DIR, instance.id, filename), ContentFile(_file.read()))
        path = private_storage.save(path_format_string.format(instance.id, filename), ContentFile(_file.read()))
        # path = default_storage.save(path_format_string.format(instance.id, filename), ContentFile(_file.read()))

    # comms_log doc store save
    elif comms_instance and 'filename' in request.data:
        filename = request.data.get('filename')
        _file = request.data.get('_file')

        document = comms_instance.documents.get_or_create(
            name=filename)[0]
        path = private_storage.save(
            '{}/{}/communications/{}/documents/{}'.format(
                instance._meta.model_name, instance.id, comms_instance.id, filename), ContentFile(
                _file.read()))

    # default doc store save
    elif 'filename' in request.data:
        filename = request.data.get('filename')
        _file = request.data.get('_file')

        document = instance.documents.get_or_create(
            name=filename)[0]
        path = private_storage.save(
            '{}/{}/documents/{}'.format(
                instance._meta.model_name, instance.id, filename), ContentFile(
                _file.read()))

    if document and path:
        document._file = path
        document.save()
        logger.info(f'Document: [{document}] has been saved for the proposal: [{instance}].')

# For transferring files from temp doc objs to default doc objs
def save_default_document_obj(instance, temp_document):
    document = instance.documents.get_or_create(
        name=temp_document.name)[0]
    path = private_storage.save(
        '{}/{}/documents/{}'.format(
            instance._meta.model_name, 
            instance.id, 
            temp_document.name
            ), 
            temp_document._file
        )

    document._file = path
    document.save()

def save_vessel_registration_document_obj(instance, temp_document):
    document = instance.vessel_registration_documents.get_or_create(
            input_name="vessel_registration_document",
            name=temp_document.name)[0]
    path = private_storage.save(
        '{}/{}/documents/{}'.format(
            instance._meta.model_name, 
            instance.id, 
            temp_document.name,
            ), 
            temp_document._file
        )

    document._file = path
    document.save()

