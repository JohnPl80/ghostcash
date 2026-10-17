"""Dataclass models mirroring the GhostCash SQLite schema (CLAUDE.md section 6).

Each model has a `from_row(sqlite3.Row)` classmethod for reading query
results. These are plain data containers with light validation, not an
ORM - queries stay as explicit SQL in each subsystem.
"""

from ghostcash.models.content import (
    CONTENT_STATUSES,
    VARIANT_STATUSES,
    VARIANT_TYPES,
    Content,
    ContentPerformance,
    ContentVariant,
)
from ghostcash.models.jobs import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAYS_SECONDS,
    JOB_STATUSES,
    Job,
    JobError,
)
from ghostcash.models.product import (
    OPPORTUNITY_STATUSES,
    PRODUCT_STATUSES,
    Product,
    ProductOpportunity,
)
from ghostcash.models.publishing import (
    CAMPAIGN_STATUSES,
    PLATFORM_NAMES,
    PUBLISHED_POST_STATUSES,
    Campaign,
    Platform,
    PublishedPost,
)
from ghostcash.models.system import EVENT_SEVERITIES, Setting, SystemEvent
from ghostcash.models.topic import FRESHNESS_VALUES, TOPIC_STATUSES, Topic, TopicScore

__all__ = [
    "CONTENT_STATUSES",
    "VARIANT_STATUSES",
    "VARIANT_TYPES",
    "Content",
    "ContentPerformance",
    "ContentVariant",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAYS_SECONDS",
    "JOB_STATUSES",
    "Job",
    "JobError",
    "OPPORTUNITY_STATUSES",
    "PRODUCT_STATUSES",
    "Product",
    "ProductOpportunity",
    "CAMPAIGN_STATUSES",
    "PLATFORM_NAMES",
    "PUBLISHED_POST_STATUSES",
    "Campaign",
    "Platform",
    "PublishedPost",
    "EVENT_SEVERITIES",
    "Setting",
    "SystemEvent",
    "FRESHNESS_VALUES",
    "TOPIC_STATUSES",
    "Topic",
    "TopicScore",
]
