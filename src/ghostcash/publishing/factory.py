"""Publisher / MediaHost factories - mirrors ai/factory.py's pattern so
the rest of the system stays decoupled from a specific vendor.
"""

from __future__ import annotations

from ghostcash.config.schema import Config
from ghostcash.publishing.base import Publisher, PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.media_host import GitHubReleaseMediaHost, MediaHost, MediaHostError


def get_publisher(config: Config) -> Publisher:
    provider_name = (config.publishing.provider or "").strip().lower()
    if provider_name == "buffer":
        return BufferPublisher(api_key=config.buffer_api_key)
    raise PublisherError(
        f"Unknown publishing provider: {provider_name!r}. Currently implemented: buffer."
    )


def get_media_host(config: Config) -> MediaHost:
    host_name = (config.publishing.media_host or "").strip().lower()
    if host_name == "github_release":
        return GitHubReleaseMediaHost(
            token=config.github_token,
            repo=config.publishing.github_repo,
            release_tag=config.publishing.github_release_tag,
        )
    raise MediaHostError(
        f"Unknown media_host: {host_name!r}. Currently implemented: github_release."
    )
