"""Topic generation (CLAUDE.md section 11).

generate_topics() asks the configured AIProvider for a batch of
candidate topics distributed across the configured content categories,
scores each one with the Opportunity Score formula (section 12), and
persists the valid ones to the `topics` table.

Design note on field mapping: the `topics` table (section 6/11) has a
`production_cost_score` and a `competition_score` column, while the
Opportunity Score formula (section 12) has "production simplicity" and
"competition penalty" terms. These name the same underlying concept
from opposite ends - this module asks the model to score
`production_cost_score` as *simplicity* (1 = cheap/easy to produce) so
it plugs directly into the formula's `production_simplicity` weight,
and asks for `competition_score` as *saturation* (1 = very saturated)
so it plugs into the `competition_penalty` weight. See the prompt/schema
descriptions below for the exact wording given to the model.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.config.schema import Config, ScoringWeights
from ghostcash.models.topic import FRESHNESS_VALUES, Topic
from ghostcash.optimization.weights import get_category_weights
from ghostcash.scoring.opportunity import compute_opportunity_score

CATEGORIES_PATH = Path("config/content_categories.yaml")

_RAW_TOPIC_REQUIRED_FIELDS = (
    "topic",
    "category",
    "hook",
    "target_audience",
    "freshness",
    "expires_at",
    "curiosity_score",
    "shareability_score",
    "commercial_score",
    "evergreen_score",
    "production_cost_score",
    "competition_score",
)


class TopicGenerationError(Exception):
    """Raised when topic generation cannot produce any usable topics."""


def load_categories(path: Path = CATEGORIES_PATH) -> list[str]:
    if not path.exists():
        raise TopicGenerationError(
            f"{path} not found - see config/content_categories.yaml.example in the repo"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    categories = [c["key"] for c in raw.get("categories", [])]
    if not categories:
        raise TopicGenerationError(f"No categories found in {path}")
    return categories


def _topic_item_schema(categories: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "A single, specific short-form video idea - not a generic theme.",
            },
            "category": {"type": "string", "enum": categories},
            "hook": {
                "type": "string",
                "description": "A punchy opening line for the first 2 seconds of the video.",
            },
            "target_audience": {
                "type": "string",
                "description": "Who this video is specifically for.",
            },
            "freshness": {"type": "string", "enum": list(FRESHNESS_VALUES)},
            "expires_at": {
                "type": ["string", "null"],
                "description": (
                    "ISO 8601 date this topic stops being relevant. Non-null only if "
                    "freshness is TIME_SENSITIVE, otherwise null."
                ),
            },
            "curiosity_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0 = boring, 1 = an irresistible curiosity hook.",
            },
            "shareability_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0 = nobody would share this, 1 = highly shareable.",
            },
            "commercial_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "0 = no plausible digital-product tie-in, 1 = obvious high-intent opportunity."
                ),
            },
            "evergreen_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "0 = relevant only right now, 1 = still relevant in 2 years.",
            },
            "production_cost_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How SIMPLE this is to produce as a 15-35s faceless video: "
                    "0 = hard/expensive, 1 = very simple and cheap."
                ),
            },
            "competition_score": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How SATURATED this topic already is with similar existing content: "
                    "0 = wide open, 1 = extremely saturated."
                ),
            },
        },
        "required": list(_RAW_TOPIC_REQUIRED_FIELDS),
    }


def _response_schema(categories: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"topics": {"type": "array", "items": _topic_item_schema(categories)}},
        "required": ["topics"],
    }


def _category_weight_hint(category_weights: dict[str, float]) -> str:
    """A soft, non-binding nudge toward categories that have actually
    performed well historically (CLAUDE.md sections 31, 50: "update
    category weights" -> "use these statistics to improve future
    content selection"). Empty string if nothing has been learned yet -
    a fresh system, or no category has cleared
    optimization.min_sample_size (see optimization/statistics.py and
    optimization/weights.py) - so topic generation is unaffected until
    there is real performance data to learn from. Deliberately a
    nudge, not a hard filter: section 8 says "do not manually assume a
    winning niche" - a category with a learned weight below 1.1 still
    gets its normal, undiminished share of ideas here.
    """
    strong = sorted(
        (category for category, weight in category_weights.items() if weight > 1.1),
        key=lambda category: category_weights[category],
        reverse=True,
    )
    if not strong:
        return ""
    listed = ", ".join(strong[:5])
    return (
        f" These categories have historically performed best and should get a "
        f"somewhat larger share of the ideas: {listed}. Still include ideas from the "
        "other categories too - don't abandon them."
    )


def _channel_phrase(brand_name: str = "", brand_description: str = "") -> str:
    """How a prompt names the channel: generic unless a brand is configured."""
    name = (brand_name or "").strip()
    description = " ".join((brand_description or "").split())
    if not name:
        return "a faceless content channel"
    phrase = f'the faceless content channel "{name}"'
    return f"{phrase} ({description})" if description else phrase


def _build_prompt(
    categories: list[str],
    count: int,
    category_weights: dict[str, float],
    brand_name: str = "",
    brand_description: str = "",
) -> str:
    category_list = ", ".join(categories)
    channel = _channel_phrase(brand_name, brand_description)
    return (
        f"Generate exactly {count} candidate short-form video topic ideas for {channel} "
        "(15-35 second videos, no on-camera talent, text/voiceover only). "
        f"Distribute the ideas across these categories: {category_list}. "
        "Each idea must be a specific, concrete video concept, not a generic theme. "
        "Avoid generic motivational filler, unsupported claims, fabricated statistics, "
        "plagiarized ideas, or anything requiring impersonation of a real person. "
        "Score every field honestly and independently - vary the scores realistically "
        "instead of giving every idea similar numbers."
        f"{_category_weight_hint(category_weights)}"
    )


def generate_topics(
    ai_provider: AIProvider,
    conn: sqlite3.Connection,
    config: Config,
    count: int | None = None,
    categories_path: Path | None = None,
) -> list[Topic]:
    """Generate, score, and persist a batch of candidate topics.

    Returns the Topic objects that were inserted (at least one).
    Raises TopicGenerationError if the AI response can't be turned into
    any valid topic, or if AI_MAX_REQUESTS_PER_RUN rules out even the
    single call this makes.
    """
    if config.ai.max_requests_per_run < 1:
        raise TopicGenerationError(
            "AI_MAX_REQUESTS_PER_RUN is set to 0 - refusing to make any AI calls."
        )

    requested_count = count if count is not None else config.content.topics_per_run
    if categories_path is None:
        categories_path = Path(config.brand.categories_file or CATEGORIES_PATH)
    categories = load_categories(categories_path)
    schema = _response_schema(categories)
    category_weights = get_category_weights(conn)
    prompt = _build_prompt(
        categories,
        requested_count,
        category_weights,
        config.brand.name,
        config.brand.description,
    )

    try:
        result = ai_provider.structured_generate(prompt, schema)
    except AIProviderError as exc:
        raise TopicGenerationError(f"AI provider call failed: {exc}") from exc

    raw_topics = result.get("topics") if isinstance(result, dict) else None
    if not raw_topics:
        raise TopicGenerationError(f"AI response contained no topics: {result!r}")

    weights = config.scoring_weights
    inserted: list[Topic] = []

    for raw in raw_topics:
        topic = _try_build_topic(raw, weights)
        if topic is None:
            continue
        _insert_topic(conn, topic)
        inserted.append(topic)

    if not inserted:
        raise TopicGenerationError("AI response did not contain any usable topics after validation")

    return inserted


def _try_build_topic(raw: Any, weights: ScoringWeights) -> Topic | None:
    if not isinstance(raw, dict):
        return None
    try:
        curiosity = float(raw["curiosity_score"])
        shareability = float(raw["shareability_score"])
        commercial = float(raw["commercial_score"])
        evergreen = float(raw["evergreen_score"])
        production_cost = float(raw["production_cost_score"])
        competition = float(raw["competition_score"])
        overall = compute_opportunity_score(
            {
                "curiosity": curiosity,
                "shareability": shareability,
                "commercial": commercial,
                "evergreen": evergreen,
                "production_simplicity": production_cost,
                "competition_penalty": competition,
            },
            weights,
        )
        return Topic(
            topic=str(raw["topic"]),
            category=str(raw["category"]),
            hook=raw.get("hook"),
            target_audience=raw.get("target_audience"),
            freshness=str(raw["freshness"]),
            expires_at=raw.get("expires_at") or None,
            curiosity_score=curiosity,
            shareability_score=shareability,
            commercial_score=commercial,
            evergreen_score=evergreen,
            production_cost_score=production_cost,
            competition_score=competition,
            overall_score=overall,
        )
    except (KeyError, TypeError, ValueError):
        # Malformed entry from the model (missing/bad field) - skip it
        # rather than fail the whole batch over one bad item.
        return None


def _insert_topic(conn: sqlite3.Connection, topic: Topic) -> None:
    cursor = conn.execute(
        """
        INSERT INTO topics (
            topic, category, hook, target_audience, freshness, expires_at,
            evergreen_score, curiosity_score, shareability_score, commercial_score,
            production_cost_score, competition_score, overall_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            topic.topic,
            topic.category,
            topic.hook,
            topic.target_audience,
            topic.freshness,
            topic.expires_at,
            topic.evergreen_score,
            topic.curiosity_score,
            topic.shareability_score,
            topic.commercial_score,
            topic.production_cost_score,
            topic.competition_score,
            topic.overall_score,
        ),
    )
    conn.commit()
    topic.id = cursor.lastrowid
