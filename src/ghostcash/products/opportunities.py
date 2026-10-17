"""Product opportunity detection (CLAUDE.md section 25) and the human
approval gate (sections 24, 52).

Detection signal: section 25 wants "high views + high clicks + low
product coverage". No GhostCash platform exposes a click metric (see
analytics/metrics.py), so engagement_rate substitutes for "high clicks"
here - the closest real, available measure of audience interest. A
topic qualifies when its aggregated views and engagement_rate both
clear the configured thresholds (products.opportunity_min_views /
opportunity_min_engagement_rate) AND no content under that topic
already has a product attached.

Approval gate: `detect_opportunities()` only ever writes
PENDING_APPROVAL rows - CLAUDE.md section 24 is explicit that products
are one of the few things never fully autonomous. `approve_opportunity`
/ `reject_opportunity` are the owner's decision, exposed via
`ghostcash products approve|reject`. Approval alone does not create
anything in Gumroad - see products/activation.py for why that is a
separate, later step.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.config.schema import Config
from ghostcash.models.product import ProductOpportunity


class OpportunityDetectionError(Exception):
    """Raised when opportunity detection cannot proceed at all (not
    raised just because zero topics qualified - that's a normal,
    reportable outcome, not a failure)."""


@dataclass
class _TopicSignal:
    topic_id: int
    topic: str
    category: str
    target_audience: str | None
    total_views: int
    engagement_rate: float


def _find_qualifying_topics(conn: sqlite3.Connection, config: Config) -> list[_TopicSignal]:
    """Topics whose aggregated performance clears both thresholds and
    have no product coverage yet.

    "No coverage" = no content row under this topic already has a
    product_id, and no PENDING_APPROVAL/APPROVED opportunity already
    exists for it (section 57 idempotency - don't re-propose the same
    topic on every run).
    """
    rows = conn.execute(
        """
        SELECT
            t.id AS topic_id, t.topic AS topic, t.category AS category,
            t.target_audience AS target_audience,
            SUM(cp.views) AS total_views,
            SUM(cp.likes + cp.comments + cp.shares) AS total_engagement
        FROM topics t
        JOIN content c ON c.topic_id = t.id
        JOIN content_performance cp ON cp.content_id = c.id
        WHERE NOT EXISTS (
            SELECT 1 FROM content c2
            WHERE c2.topic_id = t.id AND c2.product_id IS NOT NULL
        )
        AND NOT EXISTS (
            SELECT 1 FROM product_opportunities po
            WHERE po.topic_id = t.id AND po.status IN ('PENDING_APPROVAL', 'APPROVED')
        )
        GROUP BY t.id
        HAVING SUM(cp.views) >= ?
        ORDER BY total_views DESC
        """,
        (config.products.opportunity_min_views,),
    ).fetchall()

    signals: list[_TopicSignal] = []
    for row in rows:
        total_views = row["total_views"] or 0
        if total_views <= 0:
            continue
        engagement_rate = (row["total_engagement"] or 0) / total_views * 100
        if engagement_rate < config.products.opportunity_min_engagement_rate:
            continue
        signals.append(
            _TopicSignal(
                topic_id=row["topic_id"],
                topic=row["topic"],
                category=row["category"],
                target_audience=row["target_audience"],
                total_views=total_views,
                engagement_rate=engagement_rate,
            )
        )
    return signals


def _proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "proposed_name": {
                "type": "string",
                "description": "A specific, sellable product name.",
            },
            "problem_solved": {
                "type": "string",
                "description": "The one concrete problem this product solves for the audience.",
            },
            "contents_outline": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ordered list of what's actually included (sections/modules/templates)."
                ),
            },
            "price_recommendation": {
                "type": "number",
                "description": "Recommended price in USD for a digital product like this.",
            },
            "target_customer": {
                "type": "string",
                "description": "Who specifically would buy this.",
            },
            "sales_page_copy": {
                "type": "string",
                "description": "Short sales page copy: headline plus 2-3 persuasive sentences.",
            },
        },
        "required": [
            "proposed_name",
            "problem_solved",
            "contents_outline",
            "price_recommendation",
            "target_customer",
            "sales_page_copy",
        ],
    }


def _build_prompt(signal: _TopicSignal) -> str:
    audience = signal.target_audience or "a general audience"
    return (
        f'Content about "{signal.topic}" (category: {signal.category}) has performed well - '
        f"{signal.total_views} views and a {signal.engagement_rate:.1f}% engagement rate - and "
        "there is currently no digital product for this audience. Propose ONE specific, "
        f"sellable digital product (ebook, template, checklist, mini-course, swipe file, etc.) "
        f"for {audience} that follows naturally from this topic. Be concrete about what's "
        "included - avoid vague claims, fabricated statistics, or generic motivational filler."
    )


def _insert_opportunity(
    conn: sqlite3.Connection, signal: _TopicSignal, proposal: dict[str, Any]
) -> ProductOpportunity:
    opportunity = ProductOpportunity(
        proposed_name=str(proposal["proposed_name"]),
        topic_id=signal.topic_id,
        problem_solved=str(proposal["problem_solved"]),
        contents_outline="\n".join(str(item) for item in proposal.get("contents_outline") or []),
        price_recommendation=float(proposal["price_recommendation"]),
        target_customer=str(proposal["target_customer"]),
        sales_page_copy=str(proposal["sales_page_copy"]),
        supporting_views=signal.total_views,
        supporting_clicks=None,
        supporting_engagement_rate=signal.engagement_rate,
        status="PENDING_APPROVAL",
    )
    cursor = conn.execute(
        """
        INSERT INTO product_opportunities (
            topic_id, proposed_name, problem_solved, contents_outline,
            price_recommendation, target_customer, sales_page_copy,
            supporting_views, supporting_clicks, supporting_engagement_rate, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity.topic_id,
            opportunity.proposed_name,
            opportunity.problem_solved,
            opportunity.contents_outline,
            opportunity.price_recommendation,
            opportunity.target_customer,
            opportunity.sales_page_copy,
            opportunity.supporting_views,
            opportunity.supporting_clicks,
            opportunity.supporting_engagement_rate,
            opportunity.status,
        ),
    )
    conn.commit()
    opportunity.id = cursor.lastrowid
    return opportunity


def detect_opportunities(
    ai_provider: AIProvider,
    conn: sqlite3.Connection,
    config: Config,
    limit: int = 5,
) -> list[ProductOpportunity]:
    """Find under-monetized, high-performing topics and propose a
    product for each, via one AI call per qualifying topic.

    Returns the opportunities created (possibly empty - zero qualifying
    topics is a normal outcome, not an error). Raises
    OpportunityDetectionError only if topics qualified but the AI
    failed on every single one of them.
    """
    signals = _find_qualifying_topics(conn, config)[: max(limit, 0)]
    if not signals:
        return []

    created: list[ProductOpportunity] = []
    failures = 0
    for signal in signals:
        try:
            proposal = ai_provider.structured_generate(_build_prompt(signal), _proposal_schema())
        except AIProviderError:
            failures += 1
            continue
        try:
            created.append(_insert_opportunity(conn, signal, proposal))
        except (KeyError, TypeError, ValueError):
            failures += 1
            continue

    if not created and failures:
        raise OpportunityDetectionError(
            f"AI provider failed on all {failures} qualifying topic(s); no opportunities created."
        )
    return created


# -- approval gate (sections 24, 52) ----------------------------------------


def list_opportunities(
    conn: sqlite3.Connection, status: str | None = "PENDING_APPROVAL"
) -> list[ProductOpportunity]:
    if status:
        rows = conn.execute(
            "SELECT * FROM product_opportunities WHERE status = ? ORDER BY supporting_views DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM product_opportunities ORDER BY supporting_views DESC"
        ).fetchall()
    return [ProductOpportunity.from_row(row) for row in rows]


def get_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> ProductOpportunity | None:
    row = conn.execute(
        "SELECT * FROM product_opportunities WHERE id = ?", (opportunity_id,)
    ).fetchone()
    return ProductOpportunity.from_row(row) if row else None


def approve_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> ProductOpportunity:
    opportunity = get_opportunity(conn, opportunity_id)
    if opportunity is None:
        raise OpportunityDetectionError(f"No product opportunity with id {opportunity_id}.")
    if opportunity.status != "PENDING_APPROVAL":
        raise OpportunityDetectionError(
            f"Opportunity {opportunity_id} is {opportunity.status}, not PENDING_APPROVAL."
        )
    conn.execute(
        "UPDATE product_opportunities SET status = 'APPROVED' WHERE id = ?", (opportunity_id,)
    )
    conn.commit()
    opportunity.status = "APPROVED"
    return opportunity


def reject_opportunity(conn: sqlite3.Connection, opportunity_id: int) -> ProductOpportunity:
    opportunity = get_opportunity(conn, opportunity_id)
    if opportunity is None:
        raise OpportunityDetectionError(f"No product opportunity with id {opportunity_id}.")
    if opportunity.status != "PENDING_APPROVAL":
        raise OpportunityDetectionError(
            f"Opportunity {opportunity_id} is {opportunity.status}, not PENDING_APPROVAL."
        )
    conn.execute(
        "UPDATE product_opportunities SET status = 'REJECTED' WHERE id = ?", (opportunity_id,)
    )
    conn.commit()
    opportunity.status = "REJECTED"
    return opportunity
