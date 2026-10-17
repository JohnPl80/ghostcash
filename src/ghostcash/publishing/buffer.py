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

from typing import Any

import requests

from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform
from ghostcash.publishing.base import Publisher, PublisherError, ScheduleResult

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


class BufferPublisher(Publisher):
    def __init__(
        self, api_key: str, session: requests.Session | None = None, timeout_seconds: int = 60
    ) -> None:
        if not api_key:
            raise PublisherError(
                "Buffer API key is required. Set BUFFER_API_KEY in your .env "
                "(get one at https://publish.buffer.com/settings/api)."
            )
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

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
        return self._get_posts_by_status(organization_id, "buffer", channel_ids)

    def get_sent_posts(
        self, organization_id: str, channel_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return self._get_posts_by_status(organization_id, "sent", channel_ids)

    def _get_posts_by_status(
        self, organization_id: str, status: str, channel_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        query = """
            query GetPosts(
                $organizationId: OrganizationId!
                $status: [PostStatus!]
                $channelIds: [ChannelId!]
            ) {
                posts(input: {
                    organizationId: $organizationId
                    filter: { status: $status, channelIds: $channelIds }
                }) {
                    edges {
                        node { id text status dueAt sentAt channelId }
                    }
                }
            }
        """
        variables = {
            "organizationId": organization_id,
            "status": [status],
            "channelIds": channel_ids,
        }
        data = self._call(query, variables)
        return [edge["node"] for edge in data["posts"]["edges"]]

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
        data = self._call(query, {"input": {"id": post_id}})
        result = data["deletePost"]
        if result.get("__typename") != "DeletePostSuccess":
            raise PublisherError(f"Buffer could not delete post {post_id}: {result.get('message')}")
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
        data = self._call(query, {"id": post_id})
        return dict(data["post"])

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
        )
