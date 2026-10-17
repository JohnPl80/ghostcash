from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher


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
