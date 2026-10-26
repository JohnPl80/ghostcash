"""Automatic queueing (human_approval_mode.publishing: AUTONOMOUS) must not
overfill Buffer's per-channel queue - see publishing/buffer_queue.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghostcash.cli.commands import buffer_queue as buffer_queue_cmd
from ghostcash.cli.commands import publish as publish_cmd
from ghostcash.cli.main import main
from ghostcash.config.schema import Config
from ghostcash.publishing.base import Publisher, PublisherError, ScheduleResult
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.buffer_queue import (
    count_waiting_by_channel,
    make_queue_counter,
    read_channel_queues,
)
from ghostcash.publishing.media_host import MediaHost
from ghostcash.publishing.pipeline import publish_batch


def _mock_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = ""
    return response


def _posts_response(*nodes):
    return _mock_response(json_data={"data": {"posts": {"edges": [{"node": n} for n in nodes]}}})


def _enum_error(value):
    return _mock_response(
        json_data={
            "errors": [
                {
                    "message": f'Variable "$status" got invalid value "{value}" at "status[0]"; '
                    f'Value "{value}" does not exist in "PostStatus" enum.'
                }
            ],
            "data": None,
        }
    )


# -- Buffer's name for a queued post ----------------------------------------


def test_scheduled_posts_are_asked_for_as_scheduled_first_and_the_name_is_remembered():
    session = MagicMock()
    session.post.return_value = _posts_response({"id": "p1", "channelId": "ch1"})
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.get_scheduled_posts("org1")
    publisher.get_scheduled_posts("org1")

    assert publisher.scheduled_status == "scheduled"
    sent = [call.kwargs["json"]["variables"]["status"] for call in session.post.call_args_list]
    assert sent == [["scheduled"], ["scheduled"]]


def test_falls_back_to_the_older_name_when_buffer_rejects_the_first():
    session = MagicMock()
    session.post.side_effect = [_enum_error("scheduled"), _posts_response({"id": "p1"})]
    publisher = BufferPublisher(api_key="secret", session=session)

    posts = publisher.get_scheduled_posts("org1")

    assert posts == [{"id": "p1"}]
    assert publisher.scheduled_status == "buffer"
    session.post.side_effect = [_posts_response()]
    publisher.get_scheduled_posts("org1")
    assert session.post.call_args.kwargs["json"]["variables"]["status"] == ["buffer"]


def test_no_accepted_name_is_a_clear_error():
    session = MagicMock()
    session.post.side_effect = [_enum_error("scheduled"), _enum_error("buffer")]
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="scheduled, buffer"):
        publisher.get_scheduled_posts("org1")
    assert publisher.scheduled_status is None


def test_an_unrelated_error_is_raised_at_once_without_trying_the_next_name():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"errors": [{"message": "Not authorized"}], "data": None}
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    with pytest.raises(PublisherError, match="Not authorized"):
        publisher.get_scheduled_posts("org1")
    assert session.post.call_count == 1


# -- counting what waits ----------------------------------------------------


def test_read_channel_queues_counts_per_channel_and_sorts_due_times():
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_scheduled_posts.return_value = [
        {"id": "a", "channelId": "ch1", "dueAt": "2026-10-28T11:00:00.000Z"},
        {"id": "b", "channelId": "ch1", "dueAt": "2026-10-27T11:00:00.000Z"},
        {"id": "c", "channelId": "ch2", "dueAt": None},
        {"id": "d", "channelId": "other", "dueAt": "2026-10-27T11:00:00.000Z"},
    ]

    queues = read_channel_queues(publisher, "org1", ["ch1", "ch2", "ch3"])

    assert queues["ch1"].waiting == 2
    assert queues["ch1"].due_times == [
        "2026-10-27T11:00:00.000Z",
        "2026-10-28T11:00:00.000Z",
    ]
    assert queues["ch2"].waiting == 1
    assert queues["ch3"].waiting == 0  # asked for, nothing waiting
    assert "other" not in queues
    assert count_waiting_by_channel(publisher, "org1", ["ch1", "ch2"]) == {"ch1": 2, "ch2": 1}
    assert make_queue_counter(publisher, "org1")(["ch1"]) == {"ch1": 2}


# -- the guard in publish_batch ---------------------------------------------


class _Host(MediaHost):
    def upload(self, file_path, remote_name):
        return "https://example.com/video.mp4"


class _Publisher(Publisher):
    def __init__(self):
        self.calls: list[tuple[int, str, bool]] = []

    def schedule(self, content, platform, *, video_url=None, save_to_draft=False):
        self.calls.append((content.id, platform.name, save_to_draft))
        return ScheduleResult(
            external_post_id=f"p{len(self.calls)}",
            status="DRAFT" if save_to_draft else "SCHEDULED",
        )


def _platforms(conn):
    for name, channel in (("YOUTUBE", "ch1"), ("INSTAGRAM", "ch2")):
        conn.execute(
            "INSERT INTO platforms (name, buffer_channel_id, is_active) VALUES (?, ?, 1)",
            (name, channel),
        )
    conn.commit()


def _ready(conn, count):
    ids = []
    for index in range(count):
        cursor = conn.execute(
            "INSERT INTO content (topic, category, title, caption, status, rendered_video_path) "
            "VALUES (?, 'AI', ?, 'cap', 'READY', '/tmp/v.mp4')",
            (f"topic {index}", f"title {index}"),
        )
        ids.append(cursor.lastrowid)
    conn.commit()
    return ids


def _autonomous(cap=10):
    config = Config()
    config.human_approval_mode.publishing = "AUTONOMOUS"
    config.publishing.max_scheduled_per_channel = cap
    return config


def _status(conn, content_id):
    return conn.execute("SELECT status FROM content WHERE id = ?", (content_id,)).fetchone()[0]


def test_nothing_is_sent_while_any_channel_is_full(migrated_conn):
    _platforms(migrated_conn)
    ids = _ready(migrated_conn, 2)
    publisher = _Publisher()
    notes: list[str] = []

    outcomes = publish_batch(
        publisher,
        _Host(),
        migrated_conn,
        _autonomous(),
        queue_counter=lambda channel_ids: {"ch1": 9, "ch2": 10},
        notes=notes,
    )

    assert outcomes == []
    assert publisher.calls == []
    assert [_status(migrated_conn, i) for i in ids] == ["READY", "READY"]
    assert any(n.startswith("[hold]") and "INSTAGRAM" in n for n in notes)
    assert "YOUTUBE 9/10" in notes[0] and "INSTAGRAM 10/10" in notes[0]


def test_it_sends_only_as_many_videos_as_every_channel_has_room_for(migrated_conn):
    _platforms(migrated_conn)
    ids = _ready(migrated_conn, 3)
    publisher = _Publisher()
    notes: list[str] = []

    outcomes = publish_batch(
        publisher,
        _Host(),
        migrated_conn,
        _autonomous(),
        queue_counter=lambda channel_ids: {"ch1": 8, "ch2": 8},
        notes=notes,
    )

    assert len(outcomes) == 4  # two videos x two channels
    assert all(save_to_draft is False for _, _, save_to_draft in publisher.calls)
    assert [_status(migrated_conn, i) for i in ids] == ["SCHEDULED", "SCHEDULED", "READY"]
    assert any(n.startswith("[hold]") for n in notes)


def test_a_channel_with_less_room_limits_both(migrated_conn):
    _platforms(migrated_conn)
    _ready(migrated_conn, 3)
    publisher = _Publisher()

    outcomes = publish_batch(
        publisher,
        _Host(),
        migrated_conn,
        _autonomous(),
        queue_counter=lambda channel_ids: {"ch1": 0, "ch2": 9},
    )

    assert len(outcomes) == 2  # one video on both channels, then ch2 is full


def test_an_unreadable_queue_sends_nothing(migrated_conn):
    _platforms(migrated_conn)
    ids = _ready(migrated_conn, 2)
    publisher = _Publisher()
    notes: list[str] = []

    def broken(channel_ids):
        raise PublisherError("Buffer API HTTP error 500")

    outcomes = publish_batch(
        publisher, _Host(), migrated_conn, _autonomous(), queue_counter=broken, notes=notes
    )

    assert outcomes == []
    assert publisher.calls == []
    assert [_status(migrated_conn, i) for i in ids] == ["READY", "READY"]
    assert notes[0].startswith("[error]") and "nothing was sent" in notes[0]


def test_drafts_never_ask_buffer_for_the_queue(migrated_conn):
    _platforms(migrated_conn)
    _ready(migrated_conn, 1)
    publisher = _Publisher()

    def must_not_be_called(channel_ids):
        raise AssertionError("draft mode must not read the queue")

    outcomes = publish_batch(
        publisher, _Host(), migrated_conn, Config(), queue_counter=must_not_be_called
    )

    assert len(outcomes) == 2
    assert all(save_to_draft is True for _, _, save_to_draft in publisher.calls)


def test_a_limit_of_zero_turns_the_check_off(migrated_conn):
    _platforms(migrated_conn)
    _ready(migrated_conn, 1)
    publisher = _Publisher()

    def must_not_be_called(channel_ids):
        raise AssertionError("limit 0 must not read the queue")

    outcomes = publish_batch(
        publisher, _Host(), migrated_conn, _autonomous(cap=0), queue_counter=must_not_be_called
    )

    assert len(outcomes) == 2


# -- the commands -----------------------------------------------------------


def _channels():
    return [
        {"id": "ch1", "name": "shortcut", "displayName": "Shortcut Club", "service": "youtube"},
        {"id": "ch2", "name": "shortcutclubhq", "service": "instagram"},
        {"id": "ch3", "name": "somebody", "service": "linkedin"},
    ]


def _fake_buffer(monkeypatch, module) -> MagicMock:
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_organizations.return_value = [{"id": "org1"}]
    publisher.get_channels.return_value = _channels()
    publisher.scheduled_status = "scheduled"
    monkeypatch.setattr(module, "get_publisher", lambda config: publisher)
    return publisher


@pytest.fixture()
def project(isolated_project: Path, monkeypatch):
    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")
    return isolated_project


def test_buffer_queue_command_prints_counts_and_times(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch, buffer_queue_cmd)
    publisher.get_scheduled_posts.return_value = [
        {"id": "a", "channelId": "ch1", "dueAt": "2026-10-27T11:00:00.000Z"},
        {"id": "b", "channelId": "ch1", "dueAt": "2026-10-28T16:00:00.000Z"},
        {"id": "c", "channelId": "ch2", "dueAt": "2026-10-27T11:00:00.000Z"},
    ]

    assert main(["buffer-queue"]) == 0

    out = capsys.readouterr().out
    assert "Shortcut Club (youtube): 2 of 10 waiting" in out
    assert "shortcutclubhq (instagram): 1 of 10 waiting" in out
    assert "somebody" not in out  # a LinkedIn channel is outside GhostCash's scope
    assert "first goes out: 2026-10-27 11:00 UTC" in out
    assert "last goes out:  2026-10-28 16:00 UTC" in out
    assert "'scheduled'" in out
    assert "DRAFTS" in out  # the shipped default is APPROVAL_REQUIRED
    asked = publisher.get_scheduled_posts.call_args.args
    assert asked == ("org1", ["ch1", "ch2"])


def test_buffer_queue_command_reports_a_buffer_problem(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch, buffer_queue_cmd)
    publisher.get_scheduled_posts.side_effect = PublisherError("Buffer API HTTP error 401")

    assert main(["buffer-queue"]) == 1
    assert "[error] Buffer API HTTP error 401" in capsys.readouterr().out


def test_buffer_queue_command_needs_the_key(project, monkeypatch, capsys):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    _fake_buffer(monkeypatch, buffer_queue_cmd)

    assert main(["buffer-queue"]) == 1
    assert "BUFFER_API_KEY" in capsys.readouterr().out


def _autonomous_project(project: Path) -> None:
    config_path = project / "config" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "  publishing: APPROVAL_REQUIRED", "  publishing: AUTONOMOUS"
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(project / "data" / "ghostcash.db")
    _ready(conn, 1)
    conn.close()


def test_publish_fails_the_run_when_the_queue_cannot_be_read(project, monkeypatch, capsys):
    _autonomous_project(project)
    publisher = _fake_buffer(monkeypatch, publish_cmd)
    publisher.get_scheduled_posts.side_effect = PublisherError("Buffer API HTTP error 500")
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: _Host())

    assert main(["publish"]) == 1

    out = capsys.readouterr().out
    assert "[error] Could not read Buffer's queue" in out
    publisher.schedule.assert_not_called()


def test_publish_holds_back_quietly_when_a_queue_is_full(project, monkeypatch, capsys):
    _autonomous_project(project)
    publisher = _fake_buffer(monkeypatch, publish_cmd)
    publisher.get_scheduled_posts.return_value = [
        {"id": str(n), "channelId": "ch1"} for n in range(10)
    ]
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: _Host())

    assert main(["publish"]) == 0  # waiting for room is normal, not a failure

    out = capsys.readouterr().out
    assert "[hold] Buffer's queue is full on YOUTUBE" in out
    assert "No READY content is waiting" not in out
    publisher.schedule.assert_not_called()


# -- Buffer pages the posts query ---------------------------------------------


def _paged_response(nodes, has_next=False, end_cursor=None):
    return _mock_response(
        json_data={
            "data": {
                "posts": {
                    "edges": [{"node": n} for n in nodes],
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                }
            }
        }
    )


def test_posts_are_requested_100_at_a_time_with_page_info():
    session = MagicMock()
    session.post.return_value = _paged_response([{"id": "p1", "channelId": "ch1"}])
    publisher = BufferPublisher(api_key="secret", session=session)

    publisher.get_scheduled_posts("org1", ["ch1"])

    query = session.post.call_args.kwargs["json"]["query"]
    assert "first: 100" in query
    assert "pageInfo" in query and "hasNextPage" in query and "endCursor" in query
    assert "after:" not in query  # the first page has no cursor


def test_every_page_is_read_so_a_full_queue_is_not_undercounted():
    session = MagicMock()
    first_page = [{"id": f"a{n}", "channelId": "ch1"} for n in range(5)]
    second_page = [{"id": f"b{n}", "channelId": "ch1"} for n in range(5)]
    session.post.side_effect = [
        _paged_response(first_page, has_next=True, end_cursor="cursor-1"),
        _paged_response(second_page, has_next=False, end_cursor="cursor-2"),
    ]
    publisher = BufferPublisher(api_key="secret", session=session)

    posts = publisher.get_scheduled_posts("org1", ["ch1"])

    assert len(posts) == 10
    assert session.post.call_count == 2
    second_query = session.post.call_args_list[1].kwargs["json"]["query"]
    assert 'after: "cursor-1"' in second_query


def test_paging_stops_when_buffer_says_there_is_no_next_page():
    session = MagicMock()
    session.post.return_value = _paged_response(
        [{"id": "p1", "channelId": "ch1"}], has_next=True, end_cursor=None
    )
    publisher = BufferPublisher(api_key="secret", session=session)

    assert len(publisher.get_scheduled_posts("org1")) == 1
    assert session.post.call_count == 1  # no cursor to continue with
