import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from mooringlicensing.components.approvals.models import Sticker
from mooringlicensing.components.proposals.models import Proposal

logger = logging.getLogger('mooringlicensing')


class StickerListener(object):

    @staticmethod
    @receiver(post_save, sender=Sticker)
    def _post_save(sender, instance, **kwargs):
        if instance.status == Sticker.STICKER_STATUS_CURRENT and \
                instance.proposal_initiated and \
                instance.proposal_initiated.processing_status == Proposal.PROCESSING_STATUS_PRINTING_STICKER:
            # When a sticker has been printed, update related proposal.status
            instance.proposal_initiated.processing_status = Proposal.PROCESSING_STATUS_APPROVED
            instance.proposal_initiated.customer_status = Proposal.CUSTOMER_STATUS_APPROVED
            instance.proposal_initiated.save()

