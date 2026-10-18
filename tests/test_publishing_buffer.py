from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.factory import get_publisher


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def test_missing_api_key_raises():
    with pytest.raises(PublisherError):
        BufferPublisher(api_key="")


def test_get_account_posts_to_correct_url_with_auth_header():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"data": {"account": {"id": "acc1", "email": "a@b.com", "organizations": []}}}
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    account = publisher.get_account()

    assert account["id"] == "acc1"
    args, kwargs = session.post.call_args
    assert args[0] == "https://api.buffer.com"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"


def test_get_organizations_extracts_from_account():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "account": {
                    "id": "acc1",
                    "email": "a@b.com",
                    "organizations": [{"id": "org1", "name": "My Org"}],
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)
    orgs = publisher.get_organizations()
    assert orgs == [{"id": "org1", "name": "My Org"}]


def test_get_channels_sends_organization_id_variable():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"data": {"channels": [{"id": "ch1", "service": "youtube"}]}}
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    channels = publisher.get_channels("org1")

    assert channels == [{"id": "ch1", "service": "youtube"}]
    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["organizationId"] == "org1"


def test_create_post_success_returns_schedule_result():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "buffer", "dueAt": "2026-01-01T00:00:00Z"},
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    result = publisher.create_post(channel_id="ch1", text="Hello world")

    assert result.external_post_id == "post1"
    assert result.status == "SCHEDULED"
    assert result.scheduled_at == "2026-01-01T00:00:00Z"


def test_create_post_with_save_to_draft_sets_draft_status():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "draft", "dueAt": None},
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    result = publisher.create_post(channel_id="ch1", text="Hello", save_to_draft=True)

    assert result.status == "DRAFT"
    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["input"]["saveToDraft"] is True


def test_create_post_includes_video_asset_when_provided():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "buffer", "dueAt": None},
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.create_post(channel_id="ch1", text="Hello", video_url="https://example.com/v.mp4")

    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["input"]["assets"] == [{"video": {"url": "https://example.com/v.mp4"}}]


def test_create_post_sets_ai_assisted_flag():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "buffer", "dueAt": None},
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)
    publisher.create_post(channel_id="ch1", text="Hello")
    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["input"]["aiAssisted"] is True


def test_create_post_raises_on_mutation_error():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {"createPost": {"__typename": "MutationError", "message": "Channel not found"}}
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="Channel not found"):
        publisher.create_post(channel_id="bad", text="Hello")


def test_graphql_top_level_errors_raise_publisher_error():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"errors": [{"message": "Unauthorized", "extensions": {"code": "UNAUTHORIZED"}}]}
    )
    publisher = BufferPublisher(api_key="bad-key", session=session)

    with pytest.raises(PublisherError, match="Unauthorized"):
        publisher.get_account()


def test_http_error_raises_publisher_error():
    session = MagicMock()
    session.post.return_value = _mock_response(status_code=500, text="Internal Server Error")
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError):
        publisher.get_account()


def test_delete_post_success():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"data": {"deletePost": {"__typename": "DeletePostSuccess", "id": "post1"}}}
    )
    publisher = BufferPublisher(api_key="secret", session=session)
    assert publisher.delete_post("post1") is True


def test_delete_post_raises_on_error():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {"deletePost": {"__typename": "VoidMutationError", "message": "Not found"}}
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)
    with pytest.raises(PublisherError, match="Not found"):
        publisher.delete_post("post1")


def test_get_scheduled_posts_filters_by_buffer_status():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"data": {"posts": {"edges": [{"node": {"id": "p1", "status": "buffer"}}]}}}
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    posts = publisher.get_scheduled_posts("org1")

    assert posts == [{"id": "p1", "status": "buffer"}]
    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["status"] == ["buffer"]


def test_get_sent_posts_filters_by_sent_status():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"data": {"posts": {"edges": [{"node": {"id": "p1", "status": "sent"}}]}}}
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.get_sent_posts("org1")

    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["status"] == ["sent"]


# -- Publisher interface (schedule) -----------------------------------


def _content(**overrides) -> Content:
    defaults = dict(topic="t", category="AI", caption="A great caption", status="READY")
    defaults.update(overrides)
    return Content(**defaults)


def _platform(**overrides) -> Platform:
    defaults = dict(name="YOUTUBE", id=1, buffer_channel_id="ch1")
    defaults.update(overrides)
    return Platform(**defaults)


def test_schedule_rejects_tiktok_without_calling_api():
    session = MagicMock()
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="TIKTOK"):
        publisher.schedule(_content(), _platform(name="TIKTOK", buffer_channel_id="ch2"))

    session.post.assert_not_called()


def test_schedule_rejects_platform_without_channel_id():
    session = MagicMock()
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="buffer_channel_id"):
        publisher.schedule(_content(), _platform(buffer_channel_id=None))


def test_schedule_uses_caption_as_post_text():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "buffer", "dueAt": None},
                }
            }
        }
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(_content(caption="My caption"), _platform())

    body = session.post.call_args.kwargs["json"]
    assert body["variables"]["input"]["text"] == "My caption"
    assert body["variables"]["input"]["channelId"] == "ch1"


def test_schedule_raises_when_no_text_available():
    session = MagicMock()
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="caption"):
        publisher.schedule(_content(caption=None, title=None, topic=""), _platform())


# -- per-channel metadata Buffer requires (first live run, 2026-10-18) ---


def _ok_session() -> MagicMock:
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "data": {
                "createPost": {
                    "__typename": "PostActionSuccess",
                    "post": {"id": "post1", "status": "draft", "dueAt": None},
                }
            }
        }
    )
    return session


def _sent_input(session: MagicMock) -> dict:
    return session.post.call_args.kwargs["json"]["variables"]["input"]


def test_create_post_passes_metadata_through():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.create_post(channel_id="ch1", text="Hi", metadata={"instagram": {"type": "reel"}})

    assert _sent_input(session)["metadata"] == {"instagram": {"type": "reel"}}


def test_create_post_omits_metadata_when_none():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.create_post(channel_id="ch1", text="Hi")

    assert "metadata" not in _sent_input(session)


def test_schedule_instagram_video_is_a_reel_shared_to_feed():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(
        _content(), _platform(name="INSTAGRAM"), video_url="https://example.com/v.mp4"
    )

    assert _sent_input(session)["metadata"] == {
        "instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": True}
    }


def test_schedule_instagram_without_video_is_a_plain_post():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(_content(), _platform(name="INSTAGRAM"))

    assert _sent_input(session)["metadata"]["instagram"]["type"] == "post"


def test_schedule_youtube_sends_title_and_category():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(
        _content(title="5 Excel tricks"),
        _platform(name="YOUTUBE"),
        video_url="https://example.com/v.mp4",
    )

    assert _sent_input(session)["metadata"] == {
        "youtube": {"title": "5 Excel tricks", "categoryId": "27", "isAiGenerated": True}
    }


def test_schedule_youtube_title_is_cut_to_youtubes_100_character_limit():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(_content(title="word " * 40), _platform(name="YOUTUBE"))

    title = _sent_input(session)["metadata"]["youtube"]["title"]
    assert len(title) <= 100
    assert title.endswith("...")


def test_schedule_youtube_title_collapses_whitespace_and_falls_back_to_hook_then_topic():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.schedule(
        _content(title=None, hook="Stop  doing\nthis", topic="topic"), _platform(name="YOUTUBE")
    )
    assert _sent_input(session)["metadata"]["youtube"]["title"] == "Stop doing this"

    publisher.schedule(_content(title=None, hook=None, topic="Only a topic"), _platform())
    assert _sent_input(session)["metadata"]["youtube"]["title"] == "Only a topic"


def test_schedule_youtube_uses_configured_category():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session, youtube_category_id="28")

    publisher.schedule(_content(title="t"), _platform(name="YOUTUBE"))

    assert _sent_input(session)["metadata"]["youtube"]["categoryId"] == "28"


def test_ai_disclosure_flag_can_be_turned_off():
    session = _ok_session()
    publisher = BufferPublisher(api_key="secret", session=session, disclose_ai_generated=False)

    publisher.schedule(_content(title="t"), _platform(name="YOUTUBE"))
    assert "isAiGenerated" not in _sent_input(session)["metadata"]["youtube"]

    publisher.schedule(
        _content(title="t"), _platform(name="INSTAGRAM"), video_url="https://x/v.mp4"
    )
    assert "isAiGenerated" not in _sent_input(session)["metadata"]["instagram"]


def test_get_publisher_passes_publishing_config_to_buffer_publisher(monkeypatch):
    config = Config()
    config.buffer_api_key = "secret"
    config.publishing.youtube_category_id = "26"
    config.publishing.disclose_ai_generated = False

    publisher = get_publisher(config)

    assert isinstance(publisher, BufferPublisher)
    assert publisher._youtube_category_id == "26"
    assert publisher._disclose_ai_generated is False
