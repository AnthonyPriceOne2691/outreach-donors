"""Модели. Импортируются здесь все — иначе Alembic не увидит часть таблиц."""

from backend.features.core.models.access import AuditLogModel, UserModel
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
from backend.features.core.models.run import RunModel, RunSettingsModel

__all__ = [
    "AuditLogModel",
    "CampaignModel",
    "ContactModel",
    "DomainModel",
    "DonorModel",
    "MessageModel",
    "ReplyModel",
    "RunModel",
    "RunSettingsModel",
    "SenderModel",
    "SuppressionModel",
    "ThreadModel",
    "UsageRecordModel",
    "UserModel",
]
