from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghostcash.publishing.media_host import GitHubReleaseMediaHost, MediaHostError


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def test_missing_token_raises():
    with pytest.raises(MediaHostError, match="token"):
        GitHubReleaseMediaHost(token="", repo="owner/repo")


def test_missing_repo_raises():
    with pytest.raises(MediaHostError, match="github_repo"):
        GitHubReleaseMediaHost(token="secret", repo="")


def test_malformed_repo_raises():
    with pytest.raises(MediaHostError, match="github_repo"):
        GitHubReleaseMediaHost(token="secret", repo="not-a-valid-repo")


def test_upload_raises_for_missing_file():
    host = GitHubReleaseMediaHost(token="secret", repo="owner/repo", session=MagicMock())
    with pytest.raises(MediaHostError, match="not found"):
        host.upload("/no/such/file.mp4", "content_1.mp4")


def test_upload_creates_release_when_none_exists(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")

    session = MagicMock()

    def fake_request(method, url, **kwargs):
        if method == "GET" and url.endswith("/releases/tags/ghostcash-media"):
            return _mock_response(status_code=404)
        if method == "POST" and url.endswith("/releases"):
            return _mock_response(status_code=201, json_data={"id": 555, "assets": []})
        raise AssertionError(f"unexpected request: {method} {url}")

    session.request.side_effect = fake_request
    session.post.return_value = _mock_response(
        status_code=201,
        json_data={
            "browser_download_url": "https://release-assets.githubusercontent.com/video.mp4"
        },
    )

    host = GitHubReleaseMediaHost(token="secret", repo="owner/repo", session=session)
    url = host.upload(str(video), "content_1.mp4")

    assert url == "https://release-assets.githubusercontent.com/video.mp4"
    upload_call = session.post.call_args
    assert upload_call.args[0] == "https://uploads.github.com/repos/owner/repo/releases/555/assets"
    assert upload_call.kwargs["params"] == {"name": "content_1.mp4"}
    assert upload_call.kwargs["headers"]["Content-Type"] == "video/mp4"


def test_upload_reuses_existing_release(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")

    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=200, json_data={"id": 555, "assets": []}
    )
    session.post.return_value = _mock_response(
        status_code=201, json_data={"browser_download_url": "https://example.com/video.mp4"}
    )

    host = GitHubReleaseMediaHost(token="secret", repo="owner/repo", session=session)
    host.upload(str(video), "content_1.mp4")

    # Only the GET (existing release) - no POST to create a new release.
    get_calls = [c for c in session.request.call_args_list if c.args[0] == "GET"]
    post_release_calls = [c for c in session.request.call_args_list if c.args[0] == "POST"]
    assert len(get_calls) == 1
    assert len(post_release_calls) == 0


def test_upload_reuses_existing_asset_with_same_name(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")

    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=200,
        json_data={
            "id": 555,
            "assets": [
                {
                    "name": "content_1.mp4",
                    "browser_download_url": "https://example.com/existing.mp4",
                }
            ],
        },
    )

    host = GitHubReleaseMediaHost(token="secret", repo="owner/repo", session=session)
    url = host.upload(str(video), "content_1.mp4")

    assert url == "https://example.com/existing.mp4"
    session.post.assert_not_called()  # no re-upload needed


def test_upload_raises_on_github_error(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video bytes")

    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=403, json_data={"message": "Forbidden"}, text="Forbidden"
    )

    host = GitHubReleaseMediaHost(token="secret", repo="owner/repo", session=session)
    with pytest.raises(MediaHostError, match="Forbidden"):
        host.upload(str(video), "content_1.mp4")
