"""Saved narration, and videos that wait instead of failing or going robotic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.video.renderer import (
    RenderDeferredError,
    RetryLater,
    deferred_notice,
    prune_audio_cache,
    render_batch,
    render_content,
)
from ghostcash.video.tts import (
    FallbackTTSProvider,
    FliteTTSProvider,
    GeminiTTSProvider,
    TTSError,
    TTSProvider,
    TTSQuotaExhaustedError,
    TTSTemporaryError,
)


class CountingVoice(TTSProvider):
    """A stand-in for a metered voice service: real speech made locally,
    counting how many times it was asked (each ask costs quota)."""

    def __init__(self, identity: str | None = "test|voice") -> None:
        self.calls = 0
        self._identity = identity
        self._flite = FliteTTSProvider()

    @property
    def cache_identity(self) -> str | None:
        return self._identity

    def synthesize(self, text: str, output_path: str) -> None:
        self.calls += 1
        self._flite.synthesize(text, output_path)


class UnavailableVoice(TTSProvider):
    def __init__(self, error: Exception) -> None:
        self.calls = 0
        self._error = error

    @property
    def cache_identity(self) -> str | None:
        return "test|voice"

    def synthesize(self, text: str, output_path: str) -> None:
        self.calls += 1
        raise self._error


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.video.output_dir = str(tmp_path / "videos")
    config.video.audio_cache_dir = str(tmp_path / "audio")
    return config


def _content(
    content_id: int = 1, script: str = "Point one is short. Point two is short."
) -> Content:
    return Content(
        id=content_id,
        topic="t",
        category="AI",
        title="A title",
        hook="You waste hours.",
        script=script,
        caption="cap",
        cta="Follow now.",
        content_type="INFORMATION",
        status="APPROVED",
    )


def _insert_approved(conn, title="title", script="Short script here.", status="APPROVED") -> int:
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, hook, script, cta, content_type, status) "
        "VALUES ('t', 'AI', ?, 'hook', ?, 'cta', 'INFORMATION', ?)",
        (title, script, status),
    )
    conn.commit()
    return cursor.lastrowid


def _cached_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "audio").glob("*.flac")) if (tmp_path / "audio").exists() else []


# -- saving and reusing the narration -----------------------------------------


def test_the_narration_is_saved_and_a_second_render_costs_no_request(tmp_path):
    config = _config(tmp_path)
    voice = CountingVoice()

    first = render_content(_content(), config, tts_provider=voice)
    assert voice.calls == 1
    files = _cached_files(tmp_path)
    assert len(files) == 1 and files[0].name.startswith("content_1_")

    Path(first.output_path).unlink()  # the video vanishes, like on a fresh runner
    second = render_content(_content(), config, tts_provider=voice)

    assert voice.calls == 1  # reused, not asked again
    assert Path(second.output_path).stat().st_size > 0
    assert abs(second.duration_seconds - first.duration_seconds) < 0.5


def test_changed_script_or_voice_makes_a_new_recording(tmp_path):
    config = _config(tmp_path)
    voice = CountingVoice()
    render_content(_content(), config, tts_provider=voice)

    render_content(
        _content(script="A completely different script. It is also short."),
        config,
        tts_provider=voice,
    )
    assert voice.calls == 2
    assert len(_cached_files(tmp_path)) == 1  # the stale recording was replaced

    other_voice = CountingVoice(identity="test|other-voice")
    render_content(
        _content(script="A completely different script. It is also short."),
        config,
        tts_provider=other_voice,
    )
    assert other_voice.calls == 1


def test_a_free_local_voice_is_not_saved(tmp_path):
    config = _config(tmp_path)
    voice = CountingVoice(identity=None)

    render_content(_content(), config, tts_provider=voice)
    render_content(_content(), config, tts_provider=voice)

    assert voice.calls == 2
    assert _cached_files(tmp_path) == []


def test_audio_made_by_the_backup_voice_is_never_saved(tmp_path):
    config = _config(tmp_path)
    primary = UnavailableVoice(TTSQuotaExhaustedError("used up"))
    provider = FallbackTTSProvider(primary, FliteTTSProvider())

    render_content(_content(), config, tts_provider=provider)

    assert provider.used_fallback_last is True
    assert _cached_files(tmp_path) == []  # next run should get the good voice


def test_an_unwritable_cache_does_not_break_rendering(tmp_path):
    config = _config(tmp_path)
    (tmp_path / "audio").write_text("a file where the folder should be")

    result = render_content(_content(), config, tts_provider=CountingVoice())

    assert Path(result.output_path).stat().st_size > 0


# -- temporary unavailability ---------------------------------------------------


def test_temporary_voice_failure_defers_instead_of_failing(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(RenderDeferredError, match="not available right now"):
        render_content(
            _content(), config, tts_provider=UnavailableVoice(TTSTemporaryError("overloaded"))
        )


def test_permanent_voice_failure_is_an_ordinary_render_error(tmp_path):
    from ghostcash.video.renderer import RenderError

    config = _config(tmp_path)
    with pytest.raises(RenderError) as excinfo:
        render_content(_content(), config, tts_provider=UnavailableVoice(TTSError("key invalid")))
    assert not isinstance(excinfo.value, RenderDeferredError)


def test_batch_leaves_deferred_content_approved_not_failed(migrated_conn, tmp_path):
    first = _insert_approved(migrated_conn, title="one")
    second = _insert_approved(migrated_conn, title="two")
    voice = UnavailableVoice(TTSQuotaExhaustedError("limit: 10 requests per day"))

    outcomes = render_batch(migrated_conn, _config(tmp_path), count=2, tts_provider=voice)

    assert [result for _, result, _ in outcomes] == [None, None]
    assert all(isinstance(error, RetryLater) for _, _, error in outcomes)
    rows = migrated_conn.execute("SELECT status, rendered_video_path FROM content").fetchall()
    assert [(row["status"], row["rendered_video_path"]) for row in rows] == [("APPROVED", None)] * 2
    assert {first, second} == {content.id for content, _, _ in outcomes}


def test_a_deferred_item_is_picked_up_by_the_next_run(migrated_conn, tmp_path):
    _insert_approved(migrated_conn)
    config = _config(tmp_path)
    render_batch(
        migrated_conn, config, count=1, tts_provider=UnavailableVoice(TTSTemporaryError("x"))
    )

    outcomes = render_batch(migrated_conn, config, count=1, tts_provider=CountingVoice())

    assert outcomes[0][1] is not None
    status = migrated_conn.execute("SELECT status FROM content").fetchone()["status"]
    assert status == "READY"


def test_saved_narration_still_renders_when_the_service_is_out_of_quota(migrated_conn, tmp_path):
    content_id = _insert_approved(migrated_conn, script="Short script here.")
    config = _config(tmp_path)
    voice = CountingVoice()
    render_batch(migrated_conn, config, count=1, tts_provider=voice)
    assert voice.calls == 1

    # The video file is lost (new runner) and the service has no quota left.
    migrated_conn.execute(
        "UPDATE content SET status='APPROVED', rendered_video_path=NULL WHERE id = ?", (content_id,)
    )
    migrated_conn.commit()
    exhausted = UnavailableVoice(TTSQuotaExhaustedError("used up"))
    outcomes = render_batch(migrated_conn, config, count=1, tts_provider=exhausted)

    assert outcomes[0][1] is not None  # made from the saved audio
    assert exhausted.calls == 0


def test_a_real_failure_still_marks_failed(migrated_conn, tmp_path):
    _insert_approved(migrated_conn, script="")
    outcomes = render_batch(migrated_conn, _config(tmp_path), count=1, tts_provider=CountingVoice())

    assert not isinstance(outcomes[0][2], RetryLater)
    assert migrated_conn.execute("SELECT status FROM content").fetchone()["status"] == "FAILED"


def test_deferred_notice_only_counts_deferred_items():
    waiting = (_content(1), None, RetryLater("voice not available right now: used up"))
    failed = (_content(2), None, "ffmpeg render failed")
    assert deferred_notice([failed]) is None
    notice = deferred_notice([waiting, failed])
    assert notice and notice.startswith("[WAITING] 1 video(s)") and "used up" in notice


# -- cleaning up -----------------------------------------------------------------


def test_prune_removes_audio_only_for_content_that_no_longer_needs_it(migrated_conn, tmp_path):
    config = _config(tmp_path)
    approved = _insert_approved(migrated_conn, status="APPROVED")
    ready = _insert_approved(migrated_conn, status="READY")
    published = _insert_approved(migrated_conn, status="SCHEDULED")
    rejected = _insert_approved(migrated_conn, status="REJECTED")
    audio = tmp_path / "audio"
    audio.mkdir()
    for content_id in (approved, ready, published, rejected, 9999):
        (audio / f"content_{content_id}_abc.flac").write_bytes(b"x")
    (audio / "content_1_zzz.part.flac").write_bytes(b"x")
    (audio / "notes.txt").write_text("not ours")

    removed = prune_audio_cache(migrated_conn, config)

    left = sorted(path.name for path in audio.iterdir())
    assert left == sorted(
        [f"content_{approved}_abc.flac", f"content_{ready}_abc.flac", "notes.txt"]
    )
    assert removed == 3


def test_prune_without_a_cache_folder_is_a_noop(migrated_conn, tmp_path):
    assert prune_audio_cache(migrated_conn, _config(tmp_path)) == 0


def test_batch_prunes_audio_of_published_content(migrated_conn, tmp_path):
    config = _config(tmp_path)
    audio = tmp_path / "audio"
    audio.mkdir()
    old = _insert_approved(migrated_conn, status="SCHEDULED")
    (audio / f"content_{old}_abc.flac").write_bytes(b"x")
    _insert_approved(migrated_conn)

    render_batch(migrated_conn, config, count=1, tts_provider=CountingVoice())

    names = [path.name for path in audio.glob("*.flac")]
    assert all(not name.startswith(f"content_{old}_") for name in names)
    assert len(names) == 1  # the one just rendered (still READY) is kept


def test_gemini_provider_is_cacheable_via_the_real_class(tmp_path):
    provider = GeminiTTSProvider(api_key="k", session=MagicMock())
    assert provider.cache_identity is not None
