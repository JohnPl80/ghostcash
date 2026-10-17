"""Topic generation, content generation, quality control, duplicate detection.

CLAUDE.md sections 11-15.

Status: topic generation (section 11), content generation (section 13),
quality control (section 14), and duplicate detection (section 15) are
all implemented - see generate_topics(), generate_scripts(), and
evaluate_content(). Not implemented yet: rendering-asset validation
(needs Phase 4's renderer to exist first).
"""

from ghostcash.content.duplicates import compute_fingerprint, find_near_duplicate
from ghostcash.content.generation import (
    ContentGenerationError,
    GeneratedDraft,
    generate_draft_for_topic,
    generate_scripts,
    select_candidate_topics,
)
from ghostcash.content.quality import QualityResult, evaluate_content
from ghostcash.content.topics import TopicGenerationError, generate_topics

__all__ = [
    "ContentGenerationError",
    "GeneratedDraft",
    "QualityResult",
    "TopicGenerationError",
    "compute_fingerprint",
    "evaluate_content",
    "find_near_duplicate",
    "generate_draft_for_topic",
    "generate_scripts",
    "generate_topics",
    "select_candidate_topics",
]
