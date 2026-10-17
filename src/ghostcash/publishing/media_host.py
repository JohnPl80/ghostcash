"""MediaHost: gets a rendered local video onto a public, permanent URL.

Buffer's API does not accept file uploads for post assets - it needs a
stable HTTPS URL it can fetch the file from itself (see
developers.buffer.com/guides/hosting-media.html, which itself suggests
Cloudinary or Cloudflare R2). GhostCash's rendered videos
(assets/generated/*.mp4) only exist locally, so something has to bridge
that gap before a post with a video can be created.

GitHubReleaseMediaHost is the default because it needs zero new
external accounts: GitHub is already required (CLAUDE.md section 54),
and GitHub Actions injects a usable token automatically. Uploading to a
release asset and using its public `browser_download_url` is a
supported, if slightly unconventional, use of GitHub's REST API for
this purpose - swap in Cloudinary/R2 via a new MediaHost implementation
if you'd rather not use it this way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests


class MediaHostError(Exception):
    """Raised when uploading a local file to get a public URL fails."""


class MediaHost:
    def upload(self, file_path: str, remote_name: str) -> str:
        """Upload the local file at `file_path` and return a public,
        fetchable HTTPS URL for it."""
        raise NotImplementedError


class GitHubReleaseMediaHost(MediaHost):
    API_BASE = "https://api.github.com"
    UPLOAD_BASE = "https://uploads.github.com"
    API_VERSION = "2022-11-28"

    def __init__(
        self,
        token: str,
        repo: str,
        release_tag: str = "ghostcash-media",
        session: requests.Session | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        if not token:
            raise MediaHostError(
                "A GitHub token is required for the github_release media host. "
                "Set GITHUB_TOKEN in your .env (GitHub Actions provides one "
                "automatically - no setup needed there)."
            )
        if not repo or "/" not in repo:
            raise MediaHostError(
                "publishing.github_repo must be set to 'owner/repo' to use the "
                "github_release media host."
            )
        self._token = token
        self._repo = repo
        self._release_tag = release_tag
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            response = self._session.request(
                method, url, headers=self._headers(), timeout=self._timeout_seconds, **kwargs
            )
        except requests.RequestException as exc:
            raise MediaHostError(f"Could not reach GitHub: {exc}") from exc
        return response

    def _get_or_create_release(self) -> dict[str, Any]:
        get_url = f"{self.API_BASE}/repos/{self._repo}/releases/tags/{self._release_tag}"
        response = self._request("GET", get_url)
        if response.status_code == 200:
            data: dict[str, Any] = response.json()
            return data
        if response.status_code != 404:
            raise MediaHostError(self._format_error("fetching release", response))

        create_url = f"{self.API_BASE}/repos/{self._repo}/releases"
        response = self._request(
            "POST",
            create_url,
            json={
                "tag_name": self._release_tag,
                "name": self._release_tag,
                "body": "Media assets uploaded by GhostCash for Buffer post attachments.",
                "draft": False,
                "prerelease": False,
            },
        )
        if not response.ok:
            raise MediaHostError(self._format_error("creating release", response))
        created: dict[str, Any] = response.json()
        return created

    def upload(self, file_path: str, remote_name: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise MediaHostError(f"File not found: {file_path}")

        release = self._get_or_create_release()
        release_id = release["id"]

        # If an asset with this name already exists on the release (e.g.
        # a re-run), reuse its URL instead of failing on a name clash.
        existing = self._find_existing_asset(release, remote_name)
        if existing is not None:
            return str(existing["browser_download_url"])

        upload_url = f"{self.UPLOAD_BASE}/repos/{self._repo}/releases/{release_id}/assets"
        content_type = "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"
        headers = {**self._headers(), "Content-Type": content_type}

        try:
            with path.open("rb") as fh:
                response = self._session.post(
                    upload_url,
                    headers=headers,
                    params={"name": remote_name},
                    data=fh,
                    timeout=self._timeout_seconds,
                )
        except requests.RequestException as exc:
            raise MediaHostError(f"Could not reach GitHub uploads endpoint: {exc}") from exc

        if not response.ok:
            raise MediaHostError(self._format_error("uploading asset", response))

        payload: dict[str, Any] = response.json()
        return str(payload["browser_download_url"])

    @staticmethod
    def _find_existing_asset(release: dict[str, Any], name: str) -> dict[str, Any] | None:
        for asset in release.get("assets", []) or []:
            if asset.get("name") == name:
                return asset
        return None

    @staticmethod
    def _format_error(action: str, response: requests.Response) -> str:
        try:
            payload = response.json()
            message = payload.get("message", response.text)
        except ValueError:
            message = response.text
        return f"GitHub error while {action} ({response.status_code}): {message}"
