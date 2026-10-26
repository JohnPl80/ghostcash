"""`ghostcash buffer-queue` - what is waiting in Buffer's queue, per channel.

Read-only. It asks Buffer how many posts are scheduled on each channel and
when the first and last of them go out, so the numbers can be compared with
Buffer's own calendar. It is also the quickest way to confirm that Buffer
answers the "what is scheduled?" question the way GhostCash expects before
videos are queued automatically (human_approval_mode.publishing:
AUTONOMOUS - see publishing/buffer_queue.py).

It needs only BUFFER_API_KEY (no database), so it can be run on any
machine that has the key in its .env.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import SERVICE_TO_PLATFORM, BufferPublisher
from ghostcash.publishing.buffer_queue import read_channel_queues
from ghostcash.publishing.channels import resolve_organization_id
from ghostcash.publishing.factory import get_publisher


def _format_due(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    missing = validate_environment(["publishing"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        return 1

    try:
        publisher = get_publisher(config)
        if not isinstance(publisher, BufferPublisher):
            print("[error] this command is Buffer-specific.")
            return 1
        organization_id = resolve_organization_id(publisher, config)
        channels = [
            channel
            for channel in publisher.get_channels(organization_id)
            if str(channel.get("service", "")).lower() in SERVICE_TO_PLATFORM
        ]
        queues = read_channel_queues(
            publisher, organization_id, [str(channel["id"]) for channel in channels]
        )
    except PublisherError as exc:
        print(f"[error] {exc}")
        return 1

    limit = config.publishing.max_scheduled_per_channel
    autonomous = config.human_approval_mode.publishing.strip().upper() == "AUTONOMOUS"
    print("Buffer queue (posts waiting for their time slot)")
    print("=" * 48)
    if not channels:
        print("No YouTube, Instagram or TikTok channels are connected to this Buffer account.")
    for channel in channels:
        queue = queues[str(channel["id"])]
        name = channel.get("displayName") or channel.get("name") or channel["id"]
        limit_text = f" of {limit}" if limit > 0 else ""
        print(f"{name} ({channel.get('service')}): {queue.waiting}{limit_text} waiting")
        if queue.due_times:
            print(f"    first goes out: {_format_due(queue.due_times[0])}")
            print(f"    last goes out:  {_format_due(queue.due_times[-1])}")

    print()
    if publisher.scheduled_status:
        print(f"Buffer's name for a queued post: '{publisher.scheduled_status}'.")
    print(
        "New videos are sent to Buffer "
        + (
            "straight into the queue (human_approval_mode.publishing is AUTONOMOUS)."
            if autonomous
            else "as DRAFTS that you queue by hand (human_approval_mode.publishing is "
            "APPROVAL_REQUIRED)."
        )
    )
    print("Compare the counts above with Buffer's calendar before relying on them.")
    return 0
