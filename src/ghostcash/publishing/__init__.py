"""Publisher interface + BufferPublisher (CLAUDE.md sections 19-22).

`get_publisher(config)` / `get_media_host(config)` return the configured
implementations (Buffer / GitHub Releases today). `publish_batch()`
orchestrates uploading a video once and posting it to every active
platform, recording an idempotent published_posts row per attempt.
"""

from ghostcash.publishing.base import Publisher, PublisherError, ScheduleResult
from ghostcash.publishing.buffer import (
    SERVICE_TO_PLATFORM,
    UNSUPPORTED_FOR_POSTING,
    BufferPublisher,
)
from ghostcash.publishing.channels import SyncResult, sync_channels
from ghostcash.publishing.factory import get_media_host, get_publisher
from ghostcash.publishing.media_host import GitHubReleaseMediaHost, MediaHost, MediaHostError
from ghostcash.publishing.pipeline import (
    PublishingSetupError,
    PublishOutcome,
    load_active_platforms,
    publish_batch,
    select_ready_content,
)
from ghostcash.publishing.queue import QueueStatus, get_queue_status

__all__ = [
    "SERVICE_TO_PLATFORM",
    "UNSUPPORTED_FOR_POSTING",
    "BufferPublisher",
    "GitHubReleaseMediaHost",
    "MediaHost",
    "MediaHostError",
    "PublishOutcome",
    "Publisher",
    "PublisherError",
    "PublishingSetupError",
    "QueueStatus",
    "ScheduleResult",
    "SyncResult",
    "get_media_host",
    "get_publisher",
    "get_queue_status",
    "load_active_platforms",
    "publish_batch",
    "select_ready_content",
    "sync_channels",
]
