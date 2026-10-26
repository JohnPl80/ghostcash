"""How full is each Buffer channel's queue?

Buffer's free plan holds at most 10 scheduled posts per channel. When
GhostCash queues videos by itself (human_approval_mode.publishing:
AUTONOMOUS) it must not send more than a channel can take: a video that
lands on one channel and is refused on the other would stay half posted.
`publish_batch` therefore asks for these counts first (see pipeline.py), and
`ghostcash buffer-queue` prints the same numbers so they can be compared
with Buffer's own calendar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ghostcash.publishing.buffer import BufferPublisher


@dataclass
class ChannelQueue:
    channel_id: str
    waiting: int
    # Due times (as Buffer reports them) of the waiting posts, earliest first.
    due_times: list[str]


def read_channel_queues(
    publisher: BufferPublisher, organization_id: str, channel_ids: list[str]
) -> dict[str, ChannelQueue]:
    """Per channel: how many posts wait in Buffer's queue. Every requested
    channel is present in the result (0 when nothing waits). Posts that
    belong to other channels are ignored."""
    queues = {cid: ChannelQueue(channel_id=cid, waiting=0, due_times=[]) for cid in channel_ids}
    for post in publisher.get_scheduled_posts(organization_id, channel_ids):
        channel_id = str(post.get("channelId", ""))
        queue = queues.get(channel_id)
        if queue is None:
            continue
        queue.waiting += 1
        due = post.get("dueAt")
        if due:
            queue.due_times.append(str(due))
    for queue in queues.values():
        queue.due_times.sort()
    return queues


def count_waiting_by_channel(
    publisher: BufferPublisher, organization_id: str, channel_ids: list[str]
) -> dict[str, int]:
    """Same as read_channel_queues(), reduced to the counts."""
    queues = read_channel_queues(publisher, organization_id, channel_ids)
    return {cid: queue.waiting for cid, queue in queues.items()}


def make_queue_counter(
    publisher: BufferPublisher, organization_id: str
) -> Callable[[list[str]], dict[str, int]]:
    """The `queue_counter` that `publish_batch` expects, bound to one
    Buffer account."""

    def counter(channel_ids: list[str]) -> dict[str, int]:
        return count_waiting_by_channel(publisher, organization_id, channel_ids)

    return counter
