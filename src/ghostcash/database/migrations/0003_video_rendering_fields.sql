-- 0003_video_rendering_fields.sql
-- Adds fields needed by the video renderer (CLAUDE.md section 16) to
-- know what was actually produced for a piece of content, and for
-- `ghostcash render` to avoid re-rendering content that already has a
-- video (idempotency, per section 57).

ALTER TABLE content ADD COLUMN rendered_video_path TEXT;
ALTER TABLE content ADD COLUMN rendered_duration_seconds REAL;
