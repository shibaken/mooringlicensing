import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from mooringlicensing.components.payments_ml.models import FeeConstructor, ApplicationFee
# from ledger.payments.models import CashTransaction

from mooringlicensing.components.proposals.models import Proposal

# logger = logging.getLogger('mooringlicensing')
logger = logging.getLogger(__name__)


class FeeConstructorListener(object):

    @staticmethod
    @receiver(post_save, sender=FeeConstructor)
    def _post_save(sender, instance, **kwargs):
        instance.reconstruct_fees()


class InvoiceListerner(object):
    '''
    Caution: Once the ledger is segregated, this function doesn't work
    '''
    pass

    # TODO: Update the proposal status when cash payment

    # @staticmethod
    # @receiver(post_save, sender=CashTransaction)
    # def _post_save(sender, instance, **kwargs):
    #     # Expecting this function is called after 'Record Payment'
    #     if instance.invoice and instance.invoice.payment_status in ('paid', 'over_paid',):
    #         application_fee = ApplicationFee.objects.get(invoice_reference=instance.invoice.reference)
    #
    #         # Update proposal status
    #         if application_fee.proposal:
    #             if application_fee.proposal.processing_status == Proposal.PROCESSING_STATUS_AWAITING_PAYMENT:
    #                 application_fee.proposal.processing_status = Proposal.PROCESSING_STATUS_WITH_ASSESSOR
    #             application_fee.proposal.save()
