import logging

from django.conf import settings
from django.core.files.base import ContentFile
#from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives, EmailMessage
from django.utils.encoding import smart_text

from mooringlicensing.components.users.models import EmailUserLogEntry, private_storage


def _log_user_email(email_message, target_email_user, customer, sender=None, attachments=[]):
    # from ledger.accounts.models import EmailUserLogEntry
    if isinstance(email_message, (EmailMultiAlternatives, EmailMessage,)):
        # TODO this will log the plain text body, should we log the html instead
        text = email_message.body
        subject = email_message.subject
        fromm = smart_text(sender) if sender else smart_text(email_message.from_email)
        # the to email is normally a list
        if isinstance(email_message.to, list):
            to = ','.join(email_message.to)
        else:
            to = smart_text(email_message.to)
        # we log the cc and bcc in the same cc field of the log entry as a ',' comma separated string
        all_ccs = []
        if email_message.cc:
            all_ccs += list(email_message.cc)
        if email_message.bcc:
            all_ccs += list(email_message.bcc)
        all_ccs = ','.join(all_ccs)

    else:
        text = smart_text(email_message)
        subject = ''
        to = customer
        fromm = smart_text(sender) if sender else settings.SYSTEM_NAME_SHORT + ' Automated Message'
        all_ccs = ''

    customer = customer

    staff = sender

    kwargs = {
        'subject': subject,
        'text': text,
        # 'emailuser': target_email_user if target_email_user else customer,
        'email_user_id': target_email_user if target_email_user else customer,
        'customer': customer,
        'staff': staff,
        'to': to,
        'fromm': fromm,
        'cc': all_ccs
    }

    email_entry = EmailUserLogEntry.objects.create(**kwargs)

    for attachment in attachments:
        path_to_file = '{}/emailuser/{}/communications/{}'.format(settings.MEDIA_APP_DIR, target_email_user, attachment[0])
        path = private_storage.save(path_to_file, ContentFile(attachment[1]))
        email_entry.documents.get_or_create(_file=path_to_file, name=attachment[0])

    # return email_entry
    return None
