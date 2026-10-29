"""Модели. Импортируются здесь все — иначе Alembic не увидит часть таблиц."""

from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.form_letter import FormLetterModel
from backend.features.core.models.ops import (
    AhrefsCapModel,
    AhrefsKeySnapshotModel,
    LlmCapModel,
    SuppressionModel,
    UsageRecordModel,
)
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    QueueAutosendModel,
    ReplyModel,
    SenderHealthModel,
    SenderModel,
    SendingDomainModel,
    ThreadModel,
)
from backend.features.core.models.run import (
    RunCandidateModel,
    RunModel,
    RunSettingsModel,
    RunVerdictModel,
)
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
from backend.features.sales.segment_models import (
    SalesSegmentCandidateModel,
    SalesSegmentHelpModel,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.weekly_models import SalesWeeklyReportModel

__all__ = [
    "AdvertiserModel",
    "AgentDraftModel",
    "AgentSettingsModel",
    "AhrefsCapModel",
    "AhrefsKeySnapshotModel",
    "AuditLogModel",
    "CampaignModel",
    "CandidateMemoryModel",
    "CandidateModel",
    "ContactModel",
    "CrawlRunModel",
    "DomainModel",
    "DonorModel",
    "FormLetterModel",
    "LlmCapModel",
    "MessageModel",
    "OutLinkModel",
    "OutgoingAttachmentModel",
    "QueueAutosendModel",
    "ReplyAttachmentModel",
    "ReplyModel",
    "RunCandidateModel",
    "RunModel",
    "RunSettingsModel",
    "RunVerdictModel",
    "SalesChainTemplateModel",
    "SalesDraftNoticeModel",
    "SalesHandoffModel",
    "SalesHypothesisModel",
    "SalesKbEntryModel",
    "SalesLeadModel",
    "SalesSegmentCandidateModel",
    "SalesSegmentHelpModel",
    "SalesSegmentRunModel",
    "SalesSegmentVerdictModel",
    "SalesSettingsModel",
    "SalesStoplistModel",
    "SalesThreadModel",
    "SalesWeeklyReportModel",
    "SenderHealthModel",
    "SenderModel",
    "SendingDomainModel",
    "SupplierDonorModel",
    "SuppressionModel",
    "ThreadModel",
    "UsageRecordModel",
    "UserModel",
]
