-- 0002_content_generation_fields.sql
-- Adds fields needed by content generation (CLAUDE.md section 13) that
-- the original section-6 table list didn't give a column for:
--   - "Suggested visual sequence" -> visual_sequence_json (JSON array of
--     short strings, one per on-screen beat; consumed by the video
--     renderer in Phase 4)
--   - "Suggested product" -> suggested_product_description (free text;
--     there is no real product yet to foreign-key to at this stage -
--     turning a suggestion into an actual product is the section 24/25
--     human-approval flow, built in Phase 8)
--
-- ("Alternative hook", the other section 13 output, doesn't need a new
-- column - it's stored as a row in the existing content_variants table,
-- which was already designed for exactly this shape of data.)

ALTER TABLE content ADD COLUMN visual_sequence_json TEXT;
ALTER TABLE content ADD COLUMN suggested_product_description TEXT;
