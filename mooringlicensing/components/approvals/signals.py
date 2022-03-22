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
            stickers_being_printed = Sticker.objects.filter(
                proposal_initiated=instance.proposal_initiated,
                status__in=[
                    Sticker.STICKER_STATUS_READY,
                    Sticker.STICKER_STATUS_NOT_READY_YET,
                    Sticker.STICKER_STATUS_AWAITING_PRINTING,
                    Sticker.STICKER_STATUS_TO_BE_RETURNED,
                ])
            if not stickers_being_printed:
                # When a sticker gets 'current' status and there are no stickers with being-printed statuses, update related proposal.status
                instance.proposal_initiated.processing_status = Proposal.PROCESSING_STATUS_APPROVED
                instance.proposal_initiated.customer_status = Proposal.CUSTOMER_STATUS_APPROVED
                instance.proposal_initiated.save()

