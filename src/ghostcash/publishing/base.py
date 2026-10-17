"""Publisher interface (CLAUDE.md section 19).

CLAUDE.md's sketch is `class Publisher: def schedule(self, content): ...`
Extended here in two ways the sketch couldn't express:

  - `platform`: a single piece of content is meant to go out on several
    configured platforms at once (section 21), so the interface needs
    to say which one each call targets.
  - `video_url` / `save_to_draft`: getting a local render onto a public
    URL is a separate concern (see media_host.py) from posting it, and
    draft-vs-scheduled is a policy decision from
    config.human_approval_mode.publishing, not something a Publisher
    should read config for itself. Both are passed in by the caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform


class PublisherError(Exception):
    """Raised for any publishing provider failure: network, auth, a
    channel/platform combination that isn't supported, validation, etc."""


@dataclass
class ScheduleResult:
    external_post_id: str
    status: str  # "DRAFT" or "SCHEDULED" - see PUBLISHED_POST_STATUSES
    scheduled_at: str | None = None


class Publisher(ABC):
    @abstractmethod
    def schedule(
        self,
        content: Content,
        platform: Platform,
        *,
        video_url: str | None = None,
        save_to_draft: bool = False,
    ) -> ScheduleResult:
        """Create a post for `content` on `platform`.

        Raises PublisherError if this platform/content combination can't
        be published (e.g. the provider doesn't support the platform, or
        the API call fails). Callers are expected to catch this per
        platform and continue with the others (section 21).
        """
