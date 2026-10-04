"""Модели. Импортируются здесь все — иначе Alembic не увидит часть таблиц."""

from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel, RunSettingsModel
from backend.features.sales.models import (
    SalesHypothesisModel,
    SalesLeadModel,
    SalesStoplistModel,
)

__all__ = [
    "AdvertiserModel",
    "AuditLogModel",
    "CampaignModel",
    "CandidateModel",
    "ContactModel",
    "CrawlRunModel",
    "DomainModel",
    "DonorModel",
    "MessageModel",
    "OutLinkModel",
    "ReplyAttachmentModel",
    "ReplyModel",
    "RunCandidateModel",
    "RunModel",
    "RunSettingsModel",
    "SalesHypothesisModel",
    "SalesLeadModel",
    "SalesStoplistModel",
    "SenderModel",
    "SupplierDonorModel",
    "SuppressionModel",
    "ThreadModel",
    "UsageRecordModel",
    "UserModel",
]
