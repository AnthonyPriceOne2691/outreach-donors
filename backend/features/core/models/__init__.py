"""Модели. Импортируются здесь все — иначе Alembic не увидит часть таблиц."""

from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderHealthModel,
    SenderModel,
    SendingDomainModel,
    ThreadModel,
)
from backend.features.core.models.run import RunCandidateModel, RunModel, RunSettingsModel
from backend.features.sales.models import (
    SalesChainTemplateModel,
    SalesDraftNoticeModel,
    SalesHandoffModel,
    SalesHypothesisModel,
    SalesKbEntryModel,
    SalesLeadModel,
    SalesSettingsModel,
    SalesStoplistModel,
    SalesThreadModel,
)

__all__ = [
    "AdvertiserModel",
    "AgentDraftModel",
    "AgentSettingsModel",
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
    "SalesChainTemplateModel",
    "SalesDraftNoticeModel",
    "SalesHandoffModel",
    "SalesHypothesisModel",
    "SalesKbEntryModel",
    "SalesLeadModel",
    "SalesSettingsModel",
    "SalesStoplistModel",
    "SalesThreadModel",
    "SenderHealthModel",
    "SenderModel",
    "SendingDomainModel",
    "SupplierDonorModel",
    "SuppressionModel",
    "ThreadModel",
    "UsageRecordModel",
    "UserModel",
]
