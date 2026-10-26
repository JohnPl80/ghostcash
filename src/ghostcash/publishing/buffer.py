"""BufferPublisher (CLAUDE.md sections 19-22).

Implemented against Buffer's current official GraphQL API (verified
2026-09-13 against developers.buffer.com - endpoint, auth header, and
every query/mutation shape below checked against the live reference and
guide pages, not recalled from training data). CLAUDE.md explicitly
warns against inventing GraphQL mutations; nothing here does.

Known limitation, not a bug: Buffer's public API does not currently
support creating posts for TikTok (see posts-and-scheduling.html's
supported-platforms list - Instagram, Threads, LinkedIn, X, Facebook,
Google Business Profiles, Mastodon, YouTube, Pinterest, Bluesky).
`schedule()` raises PublisherError immediately for a TikTok platform
rather than attempting the call, so callers can treat it exactly like
any other per-channel failure (section 21's "continue operating if one
channel fails").
"""

from __future__ import annotations

import json
from typing import Any

import requests

from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform
from ghostcash.publishing.base import (
    PostNotFoundError,
    Publisher,
    PublisherError,
    ScheduleResult,
)

API_BASE = "https://api.buffer.com"

# Buffer channel `service` value -> GhostCash platform name. Only
# services GhostCash's schema currently models (YOUTUBE/TIKTOK/
# INSTAGRAM, per CLAUDE.md section 21) are mapped; other connected
# channels (LinkedIn, X, etc.) are outside GhostCash's current scope
# and are skipped when syncing channels (see channels.py).
SERVICE_TO_PLATFORM: dict[str, str] = {
    "youtube": "YOUTUBE",
    "tiktok": "TIKTOK",
    "instagram": "INSTAGRAM",
}

# Confirmed unsupported for post *creation* via Buffer's public API today
# (channels can still be connected/synced - see channels.py).
UNSUPPORTED_FOR_POSTING: frozenset[str] = frozenset({"TIKTOK"})

# Buffer validates per-channel metadata when a post is created and rejects
# it without the required fields (seen in the first live run, 2026-10-18:
# "Instagram posts require a type (post, story, or reel)" and "YouTube posts
# require a title ... require a category"). Field names below are from
# developers.buffer.com/types/{PostInputMetaData,InstagramPostMetadataInput,
# YoutubePostMetadataInput}.md, read 2026-10-18.
#
# YouTube category ids Buffer accepts (YoutubePostMetadataInput.categoryId):
# 1 Film & Animation, 2 Autos & Vehicles, 10 Music, 15 Pets & Animals,
# 17 Sports, 19 Travel & Events, 20 Gaming, 22 People & Blogs, 23 Comedy,
# 24 Entertainment, 25 News & Politics, 26 Howto & Style, 27 Education,
# 28 Science & Technology, 29 Nonprofits & Activism.
DEFAULT_YOUTUBE_CATEGORY_ID = "27"  # Education - GhostCash posts how-to tips
YOUTUBE_TITLE_MAX_CHARS = 100  # YouTube's own title limit


# Phrases in a Buffer error that mean "that post is gone" (the owner deleted
# the draft), as opposed to a real failure such as a bad key or no network.
_MISSING_POST_HINTS = (
    "not found",
    "does not exist",
    "doesn't exist",
    "no such post",
    "has been deleted",
    "was deleted",
)


def _looks_like_missing_post(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _MISSING_POST_HINTS)


# Buffer's guides: "up to 100 items per request"; more pages are followed
# with pageInfo.endCursor. 5 pages of 100 is far beyond any real queue.
POSTS_PAGE_SIZE = 100
MAX_POST_PAGES = 5

# What Buffer calls a post that waits in the queue for its time slot. Its
# guide names the state "Scheduled"; older material says "buffer". Which of
# the two the `status` filter accepts has not been confirmed against the
# live API, so both are tried (in this order) and the working one is
# remembered. `ghostcash buffer-queue` shows which one worked.
SCHEDULED_STATUS_CANDIDATES: tuple[str, ...] = ("scheduled", "buffer")

# Phrases in a GraphQL validation error that mean "that status value is not
# one this API knows", as opposed to a real failure such as a bad key.
_BAD_STATUS_HINTS = ("poststatus", "enum", "invalid value", "$status")


def _looks_like_bad_status_value(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _BAD_STATUS_HINTS)


class BufferPublisher(Publisher):
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout_seconds: int = 60,
        youtube_category_id: str = DEFAULT_YOUTUBE_CATEGORY_ID,
        disclose_ai_generated: bool = True,
    ) -> None:
        if not api_key:
            raise PublisherError(
                "Buffer API key is required. Set BUFFER_API_KEY in your .env "
                "(get one at https://publish.buffer.com/settings/api)."
            )
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds
        self._youtube_category_id = youtube_category_id
        self._disclose_ai_generated = disclose_ai_generated
        # Set once a "scheduled" status value has been accepted by Buffer.
        self.scheduled_status: str | None = None

    def _call(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._session.post(
                API_BASE,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise PublisherError(f"Could not reach the Buffer API: {exc}") from exc

        if not response.ok:
            raise PublisherError(self._format_http_error(response))

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise PublisherError(f"Buffer returned a non-JSON response: {exc}") from exc

        # GraphQL always returns HTTP 200; genuine system errors (auth,
        # rate limit, etc.) show up in this top-level array instead.
        errors = payload.get("errors")
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors)
            raise PublisherError(f"Buffer API error: {messages}")

        data = payload.get("data")
        if data is None:
            raise PublisherError(f"Buffer response had no data: {payload!r}")
        return data

    @staticmethod
    def _format_http_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("message", response.text)
        except ValueError:
            message = response.text
        return f"Buffer API HTTP error {response.status_code}: {message}"

    # -- section 20: account / organizations -----------------------------

    def get_account(self) -> dict[str, Any]:
        query = """
            query GetAccount {
                account {
                    id
                    email
                    organizations { id name }
                }
            }
        """
        data = self._call(query)
        return dict(data["account"])

    def get_organizations(self) -> list[dict[str, Any]]:
        return list(self.get_account().get("organizations", []))

    # -- section 20: channels ---------------------------------------------

    def get_channels(self, organization_id: str) -> list[dict[str, Any]]:
        query = """
            query GetChannels($organizationId: OrganizationId!) {
                channels(input: { organizationId: $organizationId }) {
                    id
                    name
                    displayName
                    service
                    isQueuePaused
                    isLocked
                    isDisconnected
                }
            }
        """
        data = self._call(query, {"organizationId": organization_id})
        return list(data["channels"])

    # -- section 20: create_post -------------------------------------------

    def create_post(
        self,
        channel_id: str,
        text: str,
        video_url: str | None = None,
        due_at: str | None = None,
        save_to_draft: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ScheduleResult:
        input_payload: dict[str, Any] = {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "automatic",
            "mode": "customScheduled" if due_at else "addToQueue",
            # GhostCash content genuinely is AI-generated - disclose it
            # rather than passing silently, matching several platforms'
            # AI-content-disclosure fields (see YoutubePostMetadata /
            # InstagramPostMetadata isAiGenerated) in spirit.
            "aiAssisted": True,
        }
        if due_at:
            input_payload["dueAt"] = due_at
        if save_to_draft:
            input_payload["saveToDraft"] = True
        if video_url:
            input_payload["assets"] = [{"video": {"url": video_url}}]
        if metadata:
            input_payload["metadata"] = metadata

        query = """
            mutation CreatePost($input: CreatePostInput!) {
                createPost(input: $input) {
                    __typename
                    ... on PostActionSuccess {
                        post { id status dueAt }
                    }
                    ... on MutationError { message }
                }
            }
        """
        data = self._call(query, {"input": input_payload})
        result = data["createPost"]
        if result.get("__typename") != "PostActionSuccess":
            raise PublisherError(f"Buffer rejected the post: {result.get('message', result)}")

        post = result["post"]
        return ScheduleResult(
            external_post_id=str(post["id"]),
            status="DRAFT" if save_to_draft else "SCHEDULED",
            scheduled_at=post.get("dueAt"),
        )

    # -- section 20: retrieving posts ---------------------------------------

    def get_scheduled_posts(
        self, organization_id: str, channel_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Posts waiting in the queue for their time slot.

        Tries each name in SCHEDULED_STATUS_CANDIDATES until Buffer accepts
        one (only a complaint about the status value itself moves on to the
        next name - any other error is raised at once), then remembers it in
        `scheduled_status`."""
        candidates = (
            (self.scheduled_status,) if self.scheduled_status else SCHEDULED_STATUS_CANDIDATES
        )
        last_error: PublisherError | None = None
        for status in candidates:
            try:
                posts = self._get_posts_by_status(organization_id, status, channel_ids)
            except PublisherError as exc:
                if _looks_like_bad_status_value(str(exc)):
                    last_error = exc
                    continue
                raise
            self.scheduled_status = status
            return posts
        raise PublisherError(
            "Buffer accepted none of the known names for a queued post "
            f"({', '.join(candidates)}): {last_error}"
        )

    def get_sent_posts(
        self, organization_id: str, channel_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return self._get_posts_by_status(organization_id, "sent", channel_ids)

    def _get_posts_by_status(
        self, organization_id: str, status: str, channel_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        """Every post with this status, following Buffer's pages.

        Without an explicit page size Buffer returned only a handful of posts
        (seen live 2026-10-26: exactly 5 per channel while the calendar held
        more), which made a nearly full queue look half empty. Buffer's
        guides show `first: 100` (its documented maximum) and
        `pageInfo { hasNextPage endCursor }`, so both are used here."""
        variables = {
            "organizationId": organization_id,
            "status": [status],
            "channelIds": channel_ids,
        }
        posts: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_POST_PAGES):
            after = f"after: {json.dumps(cursor)}" if cursor else ""
            query = f"""
                query GetPosts(
                    $organizationId: OrganizationId!
                    $status: [PostStatus!]
                    $channelIds: [ChannelId!]
                ) {{
                    posts(
                        first: {POSTS_PAGE_SIZE}
                        {after}
                        input: {{
                            organizationId: $organizationId
                            filter: {{ status: $status, channelIds: $channelIds }}
                        }}
                    ) {{
                        edges {{
                            node {{ id text status dueAt sentAt channelId }}
                        }}
                        pageInfo {{ hasNextPage endCursor }}
                    }}
                }}
            """
            data = self._call(query, variables)
            page = data["posts"]
            posts.extend(edge["node"] for edge in page["edges"])
            info = page.get("pageInfo") or {}
            cursor = info.get("endCursor")
            if not info.get("hasNextPage") or not cursor:
                break
        return posts

    # -- section 20: delete_post ---------------------------------------------

    def delete_post(self, post_id: str) -> bool:
        query = """
            mutation DeletePost($input: DeletePostInput!) {
                deletePost(input: $input) {
                    __typename
                    ... on DeletePostSuccess { id }
                    ... on VoidMutationError { message }
                }
            }
        """
        try:
            data = self._call(query, {"input": {"id": post_id}})
        except PostNotFoundError:
            raise
        except PublisherError as exc:
            # Buffer reports an unknown id as a top-level NOT_FOUND error.
            if _looks_like_missing_post(str(exc)):
                raise PostNotFoundError(
                    f"Buffer has no post {post_id} (already deleted?): {exc}"
                ) from exc
            raise
        result = data["deletePost"]
        if result.get("__typename") != "DeletePostSuccess":
            message = f"Buffer could not delete post {post_id}: {result.get('message')}"
            if _looks_like_missing_post(str(result.get("message", ""))):
                raise PostNotFoundError(message)
            raise PublisherError(message)
        return True

    # -- section 28-29: analytics / metrics --------------------------------

    def get_post_status_and_metrics(self, post_id: str) -> dict[str, Any]:
        """Fetch a single post's current status plus any collected metrics.

        Verified against developers.buffer.com/guides/post-metrics.html
        (2026-10-03). Metrics require a *personal* API key - "Post
        metrics are only available with a personal API key. App Clients
        cannot read them" (insightsRead cannot be requested through
        OAuth) - same key already used for everything else here, so no
        extra setup is needed as long as BUFFER_API_KEY is a personal
        API key per the README's Buffer setup step.

        `metrics` comes back as a possibly-empty list of
        {type, name, value, unit} objects using Buffer's normalized
        PostMetricType values (reactions, reposts, comments, shares,
        impressions, reach, views, saves, follows, quotes, viewers,
        totalTimeWatched as of the verification date above) - notably,
        no click or profile-visit type exists for any of GhostCash's
        platforms today (see analytics/metrics.py).
        """
        query = """
            query GetPostStatusAndMetrics($id: PostId!) {
                post(input: { id: $id }) {
                    id
                    status
                    dueAt
                    sentAt
                    metrics { type name value unit }
                    metricsUpdatedAt
                }
            }
        """
        try:
            data = self._call(query, {"id": post_id})
        except PostNotFoundError:
            raise
        except PublisherError as exc:
            if _looks_like_missing_post(str(exc)):
                raise PostNotFoundError(
                    f"Buffer has no post {post_id} (deleted in Buffer?): {exc}"
                ) from exc
            raise
        post = data.get("post")
        if not isinstance(post, dict) or not post:
            raise PostNotFoundError(f"Buffer has no post {post_id} (deleted in Buffer?).")
        return dict(post)

    # -- section 19/21: Publisher interface -----------------------------------

    def schedule(
        self,
        content: Content,
        platform: Platform,
        *,
        video_url: str | None = None,
        save_to_draft: bool = False,
    ) -> ScheduleResult:
        if platform.name in UNSUPPORTED_FOR_POSTING:
            raise PublisherError(
                f"Buffer's public API does not currently support creating posts for "
                f"{platform.name} (see developers.buffer.com/guides/posts-and-scheduling.html "
                "for the current supported-platforms list)."
            )
        if not platform.buffer_channel_id:
            raise PublisherError(
                f"Platform {platform.name} has no buffer_channel_id yet - sync channels first."
            )

        text = (content.caption or content.title or content.topic or "").strip()
        if not text:
            raise PublisherError("Content has no caption/title/topic to post.")

        return self.create_post(
            channel_id=platform.buffer_channel_id,
            text=text,
            video_url=video_url,
            save_to_draft=save_to_draft,
            metadata=self._build_metadata(content, platform.name, has_video=bool(video_url)),
        )

    def _build_metadata(
        self, content: Content, platform_name: str, *, has_video: bool
    ) -> dict[str, Any] | None:
        """Per-channel fields Buffer requires on top of the generic post
        input. None for a channel that needs none."""
        if platform_name == "INSTAGRAM":
            instagram: dict[str, Any] = {
                # A video is posted as a Reel; Buffer insists on an explicit type.
                "type": "reel" if has_video else "post",
                "shouldShareToFeed": True,
            }
            if self._disclose_ai_generated:
                instagram["isAiGenerated"] = True
            return {"instagram": instagram}

        if platform_name == "YOUTUBE":
            youtube: dict[str, Any] = {
                "title": self._youtube_title(content),
                "categoryId": self._youtube_category_id,
            }
            if self._disclose_ai_generated:
                youtube["isAiGenerated"] = True
            return {"youtube": youtube}

        return None

    @staticmethod
    def _youtube_title(content: Content) -> str:
        raw = content.title or content.hook or content.topic or ""
        title = " ".join(raw.split())
        if len(title) > YOUTUBE_TITLE_MAX_CHARS:
            title = title[: YOUTUBE_TITLE_MAX_CHARS - 3].rstrip() + "..."
        return title
