"""`ghostcash products <action>` (CLAUDE.md sections 23-27).

CLAUDE.md section 42's CLI list doesn't name a `products` command -
same gap as Phase 5's CLI, filled the same way (pick the simplest
reliable solution per section 1). Section 24's human-approval flow
("discover opportunity -> generate proposal -> notify owner -> owner
approves -> system publishes/activates") needs more than one verb, and
none of the other commands are a natural home for it, so `products`
gets its own nested subactions rather than a new top-level command per
verb:

  ghostcash products detect       - find opportunities, ask the AI to
                                     propose one product each (PENDING_APPROVAL)
  ghostcash products list         - show opportunities (default: pending) or
                                     --products to show existing Product rows
  ghostcash products approve ID   - owner approves a proposal
  ghostcash products reject ID    - owner rejects a proposal
  ghostcash products link ID EXT_ID
                                   - after the owner manually creates/edits
                                     the product on Gumroad (the API can't -
                                     see products/gumroad.py), attach
                                     GhostCash's record to it
  ghostcash products sync-sales   - pull new sales from Gumroad into `orders`
                                     and attribute revenue back to content
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.ai.base import AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.ai.factory import get_ai_provider
from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.products.activation import ActivationError, link_product
from ghostcash.products.base import StoreError
from ghostcash.products.factory import get_store
from ghostcash.products.opportunities import (
    OpportunityDetectionError,
    approve_opportunity,
    detect_opportunities,
    list_opportunities,
    reject_opportunity,
)
from ghostcash.products.orders import sync_orders


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)

    try:
        action = args.products_action
        if action == "detect":
            return _detect(args, config, conn)
        if action == "list":
            return _list(args, conn)
        if action == "approve":
            return _approve(args, conn)
        if action == "reject":
            return _reject(args, conn)
        if action == "link":
            return _link(args, config, conn)
        if action == "sync-sales":
            return _sync_sales(config, conn)
        print(f"[error] unknown products action: {action!r}")
        return 2
    finally:
        conn.close()


def _detect(args: argparse.Namespace, config, conn) -> int:
    missing = validate_environment(["ai"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash products detect`.")
        return 1

    provider = get_ai_provider(config.ai)
    usage_tracker = UsageTracker(conn)
    job_id = start_job(conn, "detect_product_opportunities")
    try:
        opportunities = detect_opportunities(provider, conn, config, limit=args.count or 5)
    except (AIProviderError, OpportunityDetectionError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        print(f"[error] {exc}")
        return 1

    usage = getattr(provider, "last_usage", None) or {}
    usage_tracker.record_call(
        prompt_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("output_tokens", 0)
    )
    succeed_job(conn, job_id)

    if not opportunities:
        print(
            "No qualifying topics found (needs enough views and engagement, and no "
            "existing product coverage yet - see products.opportunity_min_* in config.yaml)."
        )
        return 0

    print(f"Proposed {len(opportunities)} product opportunity(ies), pending your approval:")
    for opp in opportunities:
        print(
            f'  #{opp.id} "{opp.proposed_name}" (~${opp.price_recommendation:.0f}) - '
            f"{opp.supporting_views} views, {opp.supporting_engagement_rate:.1f}% engagement"
        )
    print(
        "\nReview with `ghostcash products list`, then "
        "`ghostcash products approve <id>` or `reject <id>`."
    )
    return 0


def _list(args: argparse.Namespace, conn) -> int:
    if getattr(args, "products", False):
        rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
        if not rows:
            print("No products yet.")
            return 0
        print(f"{len(rows)} product(s):")
        for row in rows:
            print(
                f"  #{row['id']} [{row['status']:8s}] {row['name']} "
                f"(${row['price'] or 0:.2f}, {row['store_provider']}:{row['external_product_id']})"
            )
        return 0

    status_filter = None if (args.status or "").lower() == "all" else args.status
    opportunities = list_opportunities(conn, status=status_filter)
    if not opportunities:
        label = status_filter or "any"
        print(f"No product opportunities with status={label}.")
        return 0

    print(f"{len(opportunities)} opportunity(ies) with status={status_filter or 'any'}:")
    for opp in opportunities:
        print(f'  #{opp.id} "{opp.proposed_name}" - {opp.problem_solved}')
        print(
            f"        target: {opp.target_customer} | price: ~${opp.price_recommendation:.0f} | "
            f"{opp.supporting_views} views, "
            f"{(opp.supporting_engagement_rate or 0):.1f}% engagement"
        )
    return 0


def _approve(args: argparse.Namespace, conn) -> int:
    try:
        opp = approve_opportunity(conn, args.opportunity_id)
    except OpportunityDetectionError as exc:
        print(f"[error] {exc}")
        return 1
    print(f'Approved #{opp.id} "{opp.proposed_name}".')
    print(
        "Next: create this product in your Gumroad dashboard "
        "(https://app.gumroad.com/products), then run "
        f"`ghostcash products link {opp.id} <permalink>`."
    )
    return 0


def _reject(args: argparse.Namespace, conn) -> int:
    try:
        opp = reject_opportunity(conn, args.opportunity_id)
    except OpportunityDetectionError as exc:
        print(f"[error] {exc}")
        return 1
    print(f'Rejected #{opp.id} "{opp.proposed_name}".')
    return 0


def _link(args: argparse.Namespace, config, conn) -> int:
    missing = validate_environment(["products"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash products link`.")
        return 1

    try:
        store = get_store(config)
        product = link_product(conn, store, args.opportunity_id, args.external_product_id)
    except (StoreError, ActivationError) as exc:
        print(f"[error] {exc}")
        return 1

    print(
        f"Linked opportunity #{args.opportunity_id} to product #{product.id} "
        f'"{product.name}" (${product.price or 0:.2f}) - now ACTIVE.'
    )
    return 0


def _sync_sales(config, conn) -> int:
    missing = validate_environment(["products"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash products sync-sales`.")
        return 1

    job_id = start_job(conn, "sync_product_sales")
    try:
        store = get_store(config)
        result = sync_orders(store, conn)
    except StoreError as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        print(f"[error] {exc}")
        return 1

    succeed_job(conn, job_id)
    print(
        f"Checked {result.sales_seen} sale(s): {result.orders_created} new, "
        f"{result.orders_updated} updated."
    )
    print(
        f"Revenue attributed to content: ${result.revenue_attributed:.2f} "
        f"(${result.revenue_unattributed:.2f} unattributed - no view data yet for that product)."
    )
    return 0
