# GhostCash

An autonomous faceless-content -> traffic -> digital-product revenue system.
Full product specification: [`CLAUDE.md`](./CLAUDE.md).

> **Current status: Phase 12 of 12 - MVP complete** (repository/config/
> database, AI provider + topic generation, content generation + quality
> control, the video renderer, Buffer publishing, analytics + Winner
> Score, product opportunity detection + Gumroad, a local web dashboard
> + daily report, a real GitHub Actions pipeline with cross-run
> persistence, the autonomous weekly optimization loop, and now
> `ghostcash run` / `run --dry-run` - the full pipeline end to end in
> one command, CLAUDE.md section 62's own Definition of Done). Phase 5
> (CLI) has no separate deliverable - each phase has added its own real
> CLI command as it landed, rather than stubbing the whole surface up
> front. "MVP complete" means every phase in CLAUDE.md section 61 has a
> real implementation behind it, not that every limitation below is
> gone - several are genuine current-platform-API limits (Buffer/
> TikTok, Buffer's metrics, Gumroad's product API), not gaps in this
> codebase. See [Roadmap](#roadmap) below.
>
> **What this repository can and cannot do today:** it CAN create a
> Buffer draft post with an attached video once you provide
> `BUFFER_API_KEY` and `publishing.github_repo`, collect that post's
> status/metrics and compute a Winner Score via `ghostcash analytics`,
> detect digital-product opportunities from high-performing topics and
> draft a proposal via `ghostcash products detect`, and - once you
> approve a proposal and create the product by hand in the Gumroad
> dashboard (see "Products" below for why that step is manual) - link it
> back with `ghostcash products link` and sync real sales/revenue with
> `ghostcash products sync-sales`. It CAN show you all of that - revenue,
> content, products, jobs, system health - in a local web dashboard
> (`ghostcash dashboard`) or as a plain-text summary (`ghostcash
> report`). It CAN run entirely on a schedule via GitHub Actions, with
> `data/ghostcash.db` and its backups persisting across runs, real
> AI/Buffer/Gumroad connectivity checks, and a queue-depth alert that
> fails a workflow run (and so triggers GitHub's own notification) when
> the publishing buffer runs low. It CAN learn from its own results:
> `ghostcash optimize` biases future topic/content generation toward
> what's historically performed best, replicates proven winners into a
> controlled hook A/B test, and judges those experiments once they have
> enough data. Per section 52, drafts are the default for publishing
> (`human_approval_mode.publishing: APPROVAL_REQUIRED`) - nothing goes
> out to a real audience until you review the draft in Buffer and hit
> send yourself, or explicitly switch this setting to `AUTONOMOUS`.
> Product approval (section 24) is **never** automatic, regardless of
> that setting. It CANNOT spend money, publish to TikTok (Buffer's
> public API doesn't support that - see "Publishing" below), see
> click/profile-visit metrics (Buffer doesn't expose them - see
> "Analytics" below), create/update a Gumroad product via API (Gumroad
> doesn't expose that - see "Products" below), send a notification
> through email/Discord/Telegram (no NotificationProvider is built yet -
> a failed GitHub Actions run is the real alert mechanism today). It CAN
> run the entire pipeline end to end with one command: `ghostcash run
> --dry-run` generates, renders, and reports for real while making zero
> external-API writes (section 43's mandatory pre-launch check); `ghostcash
> run` does the identical sequence with real Buffer/Gumroad calls
> wherever those credentials are configured (section 62).

## 1. What GhostCash does

GhostCash is meant to run, largely unattended, as:

```
research -> idea generation -> content generation -> quality control ->
video generation -> social distribution -> traffic -> digital product ->
sale -> automatic delivery -> analytics -> learning -> better content
```

The owner's job is to be an **operator**, not a content creator, video
editor, or customer-support rep: configure it once, approve new paid
products when asked, glance at a dashboard (`ghostcash dashboard`, or
`ghostcash report` for the same summary as plain text), and collect
revenue. Two
things are *never* fully automatic, by design: **publishing a new paid
product** and **spending money** (ads, subscriptions) - both always
require explicit human approval (CLAUDE.md sections 24 and 32).

## 2. Architecture

```
src/ghostcash/
  cli/           command-line entry point (init, health, generate...)  [Phase 1-12 - done]
  config/        YAML + env-var configuration loading                  [Phase 1 - done]
  database/      SQLite connection + migrations + job tracking         [Phase 1-2 - done]
  models/        dataclasses mirroring the schema                      [Phase 1 - done]
  ai/            AIProvider interface + GeminiProvider                 [Phase 2 - done]
  scoring/       Opportunity Score                                     [Phase 2 - done]
  content/       topic + content generation, QC, duplicate detection   [Phase 2-3 - done]
  video/         FFmpeg-based renderer, templates, TTS                 [Phase 4 - done]
  publishing/    Publisher + BufferPublisher, media hosting, queue     [Phase 6 - done]
  research/      pluggable topic research / fact-checking              [Phase 2 - not started]
  analytics/     cross-platform analytics + Winner Score               [Phase 7 - done]
  products/      Product abstraction + Gumroad store adapter           [Phase 8 - done]
  dashboard/     lightweight FastAPI + HTML dashboard                  [Phase 9 - done]
  backup.py      compressed SQLite snapshots + retention               [Phase 10 - done]
  optimization/  weekly autonomous optimization loop                   [Phase 11 - done]
  notifications/ Email / Discord / Telegram alerts                     [Phase 2+, not started]
```

No ORM and no external database: just Python's built-in `sqlite3` module,
plus a ~50-line hand-rolled migration runner (seven migrations so far).
Runtime dependencies: `PyYAML`, `requests` (Gemini, Buffer, Gumroad,
GitHub's API), `Pillow` (measuring text width when wrapping video
captions), and `fastapi` + `uvicorn` (the Phase 9 dashboard - the one
pair of dependencies CLAUDE.md section 39 names explicitly: "Use
FastAPI + simple HTML"). This is intentional (CLAUDE.md sections 4 and
67): small, boring, reliable, and cheap beats sophisticated.

### Database

All 15 tables required by CLAUDE.md section 6 (`content`, `topics`,
`products`, `jobs`, `settings`, etc.) are created by
[`src/ghostcash/database/migrations/0001_initial.sql`](src/ghostcash/database/migrations/0001_initial.sql).
Foreign keys are enforced, status columns use `CHECK` constraints, and
`published_posts.idempotency_key` is unique so a workflow re-run can
never double-publish (section 57). An `orders` table (section 27, added
by `0006_products_orders.sql`) was missing from the original section 6
list despite being required to record sales - its
`external_sale_id UNIQUE` column is this phase's idempotency key, the
same pattern as `published_posts.idempotency_key`.

### AI provider and topic generation

`ghostcash generate` calls the configured `AIProvider` (Gemini today,
via `ghostcash.ai.GeminiProvider`) to produce a batch of candidate
topics distributed across `config/content_categories.yaml`, scores each
one with the Opportunity Score formula (section 12, weights from
`config.yaml`), and writes the results to the `topics` table. Every run
is recorded in the `jobs`/`job_errors` tables (section 37), and call
counts + token usage are tracked per month in `settings` (section 46 -
see the note in `ghostcash/ai/budget.py` about why this doesn't attempt
to estimate cost in EUR yet).

The Gemini integration is built against the current `generateContent`
REST API (verified against Google's docs dated 2026-09-02) and correctly
handles a recent, easy-to-miss change: Gemini 3.x models deprecated
`temperature`/`top_p`/`top_k` in favor of a `thinking_level` control, so
this provider sends whichever parameter the selected model actually
respects instead of copying older examples that silently do nothing on
the new models.

### Content generation and quality control

After topic generation, the same `ghostcash generate` run picks the
best-scoring not-yet-used topics (`content.content_per_run`, default 5)
and asks the AI for a full draft of each: hook, script, caption, CTA, a
suggested visual sequence, an optional product idea, and one alternative
hook (stored as a `content_variants` row, ready for A/B testing later).

Every draft then goes through quality control (section 14) before it's
saved:

- **Hard gates** (reject outright, `quality_score` forced to 0): empty
  script, missing hook, missing CTA, script over the configured word
  limit, excessive repetition, a near-duplicate of existing content, or
  an invalid product link.
- **A soft 0-100 score** for everything that clears the gates, checked
  against `quality_score_threshold` (default 80).

Content that passes becomes `APPROVED` (ready for rendering once
Phase 4 exists); everything else becomes `REJECTED`. A topic whose
content generation fails outright (AI error) is left as `CANDIDATE`
rather than marked used, so it's automatically retried next run with no
extra bookkeeping.

One considered deviation worth knowing about: section 14 lists
"unsupported factual claims are detected" as a hard reject, but reliably
detecting that needs real fact-checking, not a cheap regex - a hard gate
built on a weak heuristic would block legitimate content on false
positives. This is implemented as a soft-score deduction and a flagged
reason instead. See `ghostcash/content/quality.py` for the full
reasoning and exactly which phrases it looks for.

Duplicate detection (section 15) is lexical, not semantic: it compares a
new script against recent ones with `difflib.SequenceMatcher`, plus a
SHA-256 fingerprint for exact matches. Two scripts saying the same thing
in very different words won't be caught - see
`ghostcash/content/duplicates.py` for the trade-off this makes (no
embeddings, no external API calls, no vector database - cheap and local
first, per section 4).

### Video rendering

`ghostcash render` turns every `APPROVED` piece of content without a
video yet into a finished 1080x1920 H.264/AAC MP4 (section 16):
TTS narration, template-driven text overlays with simple fade motion,
and synced captions - no stock footage, no AI video generation.

**Text-to-speech** (section 18) uses ffmpeg's own built-in `flite`
source filter rather than a separate TTS package. If your ffmpeg build
includes libflite - the standard Ubuntu/Debian `ffmpeg` package does -
speech synthesis needs *zero* dependencies beyond ffmpeg itself, which
GhostCash already requires. `ghostcash health` checks for this and
tells you plainly if your ffmpeg build lacks it.

**Templates** (section 17) are the three from the spec - Information,
List, Problem/Solution - each with a fixed scene *structure* in
`ghostcash/video/renderer.py` and a configurable *look*
(`templates/video/*.yaml`: colors, font, sizes, transition timing) that
can be tuned without touching code. A template's middle "point" scenes
come from the content's `visual_sequence` when there's enough of it,
falling back to an even split of the script's sentences otherwise.

**Two rendering bugs were found and fixed while building this**, worth
knowing about if you touch this code:

1. ffmpeg's `drawtext` filter never auto-wraps long lines - text wider
   than the frame just clips off both edges. Fixed with real font-metric
   measurement (`Pillow`'s `ImageFont.getlength`) rather than a
   hardcoded "average character width" guess, so wrapping stays correct
   if you change a template's font.
2. ffmpeg's `subtitles` filter, given a bare `.srt` file plus
   `force_style='FontSize=...'`, renders through an internal default
   script resolution that can badly mismatch the real video size - a
   `FontSize=40` style rendered at roughly **6x** too large and
   word-wrapped one word per line during testing. Fixed by generating a
   proper `.ass` file with `PlayResX`/`PlayResY` set to the real output
   resolution instead of relying on `force_style` scaling. See
   `ghostcash/video/subtitles.py`'s docstring for the full story.

### Publishing

`ghostcash publish` syncs your Buffer channels, then posts every `READY`
content item to each active platform - as a **Buffer draft** by default
(`human_approval_mode.publishing: APPROVAL_REQUIRED`, section 52), which
means nothing reaches a real audience until you open Buffer and approve
it yourself. Set that to `AUTONOMOUS` in `config.yaml` once you trust
the pipeline.

Built against Buffer's current official GraphQL API (verified
2026-09-13 against developers.buffer.com), which surfaced two things
worth knowing before you rely on this:

- **Buffer's public API does not support creating posts for TikTok**
  today (only Instagram, Threads, LinkedIn, X, Facebook, Google
  Business Profiles, Mastodon, YouTube, Pinterest, and Bluesky are
  supported for post creation). `ghostcash publish` treats this exactly
  like any other per-channel failure (section 21) - it's reported and
  skipped, not silently dropped or faked as working.
- **Buffer needs a public, permanent HTTPS URL for video attachments,
  not a file upload.** GhostCash's rendered videos only exist locally,
  so something has to bridge that gap. The default,
  `publishing.media_host: github_release`, uploads the video as a
  GitHub Release asset and uses its public download URL - the only
  option that needs no new external account, since GitHub is already
  required (section 54) and GitHub Actions provides a usable token
  automatically (`GITHUB_TOKEN`, set for you - no setup needed there).
  Outside GitHub Actions, set your own `GITHUB_TOKEN` in `.env`. Prefer
  Cloudinary or Cloudflare R2 instead? Buffer's own hosting guide
  suggests both - implement `ghostcash.publishing.media_host.MediaHost`
  and point `publishing.media_host` at it.

Every (content, platform) publish attempt is recorded in
`published_posts` with a stable idempotency key, so re-running
`ghostcash publish` never double-posts the same content to the same
platform (section 57) - already-published pairs are skipped, and a
failure on one platform never blocks the others.

### Analytics

`ghostcash analytics` polls Buffer for each tracked post's current
status and metrics, confirms posts that have actually gone out
(`DRAFT`/`SCHEDULED` -> `PUBLISHED`), and computes a **Winner Score**
for every content item with collected data (CLAUDE.md sections 28-29):

```text
Winner Score =
    0.25 engagement
  + 0.25 click-through
  + 0.35 revenue
  + 0.15 retention
```

Content at or above `analytics.winner_score_threshold` (default `0.5`)
is promoted to `WINNER` status.

Two real limitations surfaced building this, both handled by treating
"unavailable" as a distinct fact from "zero" rather than guessing:

- **Buffer exposes no click or profile-visit metric for any GhostCash
  platform today.** Verified against
  `developers.buffer.com/guides/post-metrics.html` (2026-10-03): the
  normalized `PostMetricType` values are `reactions`, `reposts`,
  `comments`, `shares`, `impressions`, `reach`, `views`, `saves`,
  `follows`, `quotes`, `viewers`, `totalTimeWatched` - no click-through
  or profile-visit equivalent. `clicks`/`profile_visits` are stored as
  `NULL` (added in migration `0005`), not `0`, and `click_rate()`
  returns `None` rather than a fabricated 0%.
- **Revenue only counts toward Winner Score once a product is actually
  attached** (`content.product_id IS NOT NULL`). A content item with no
  product has no revenue *tracking*, which is different from having
  earned `€0` - counting it as a flat 0 would wrongly punish content
  that was simply never monetized.

In both cases (and for every other Winner Score component), the
missing component's configured weight is redistributed proportionally
across whatever components *are* available, per CLAUDE.md section 29's
"if revenue data is unavailable, redistribute weights automatically" -
generalized here to any component, since in practice more than just
revenue is routinely missing before Phase 8 (products) exists.

Fetching metrics requires a Buffer **personal** API key - "Post metrics
are only available with a personal API key. App Clients cannot read
them," per Buffer's own docs. The same `BUFFER_API_KEY` used for
publishing already is one, as long as you generated it from
<https://publish.buffer.com/settings/api> rather than through an OAuth
app flow - no extra setup needed.

### Products

`ghostcash products detect` looks for topics with enough traffic and no
product yet - `SUM(views) >= products.opportunity_min_views` (default
1000) across a topic's content - and asks the AI for a full proposal
(name, problem solved, contents outline, price, target customer, sales
page copy), stored as a `PENDING_APPROVAL` row in
`product_opportunities`. You then run `ghostcash products list`,
`products approve <id>` or `products reject <id>` - CLAUDE.md section 24
makes this the one step that must stay human, so nothing here ever
auto-approves or auto-publishes a product.

One substitution from the spec, made for the same reason analytics
treats `clicks` as unavailable rather than `0` (see "Analytics" above):
section 25's opportunity signal is "high views + high clicks + low
product coverage", but Buffer exposes no click metric at all. GhostCash
substitutes the one engagement signal it *does* have -
`products.opportunity_min_engagement_rate` (default 5%, computed from
whatever reaction/share/etc. metrics Buffer provides) - rather than
gating product detection on a number that can never be collected.

**Gumroad's public API cannot create or update products** - verified
directly against `api.gumroad.com` (2026-10-04): `POST /products` and
`PUT /products/:id` both return `404` with
`"Product creation/update via API is not currently supported. Please
create products through the Gumroad dashboard."` This is a real,
current platform limitation, not a GhostCash gap, and it reshapes the
whole activation flow: after you approve an opportunity, you create the
actual product yourself in the Gumroad dashboard
(<https://app.gumroad.com/products>), then run
`ghostcash products link <opportunity_id> <gumroad_product_id>` to
record it. `link` calls Gumroad's (real, working) `GET /products/:id`
to confirm the product genuinely exists before creating the local
`products` row (`status=ACTIVE`) and attaching the topic's content to
it - it never fabricates success for a product that isn't really there.

`ghostcash products sync-sales` pages through Gumroad's `GET /sales`
(also real and working) and upserts each sale into `orders`, keyed by
`external_sale_id` for idempotent re-runs (section 57). Because Gumroad
sales carry no per-content referrer, each sale's revenue is distributed
across every content item currently promoting that product, weighted by
each item's own view count (`content_performance.views`) - the same
proportional heuristic a shared ad budget would use when individual
click attribution isn't available. This is computed as a *delta*
(`new_effective_amount - old_effective_amount`, with refunded/charged-
back sales having an effective amount of `0`), so re-syncing after a
refund or chargeback correctly claws back revenue already attributed to
`content_performance.revenue` through the exact same code path rather
than needing separate reversal logic. This is what finally lets Winner
Score's revenue component (dormant since Phase 7, gated on
`content.product_id IS NOT NULL`) compute a real, non-placeholder
number.

### Dashboard

CLAUDE.md section 39 names its stack explicitly - "FastAPI + simple
HTML, or an equally lightweight solution" - so that's what's here: a
plain FastAPI app returning hand-rolled HTML strings (no template
engine, no CSS/JS framework, no build step), started locally with
`ghostcash dashboard` and stopped with Ctrl+C. It is not deployed
anywhere and never runs in CI - section 39 calls it a "lightweight
*local* dashboard" the owner checks in on, not a service.

The six pages are exactly section 39's list - Overview (`/`), Content,
Products, Analytics, Jobs, System - and every number on them comes from
`ghostcash.dashboard.queries`, the same data-gathering module
`ghostcash report` renders as plain text. There is one source of truth
for "what does the system's current state look like," not two copies
that could quietly disagree.

Section 41's owner-facing question - "do I need to do anything?" -
drives the Overview page's banner (and `report`'s "SYSTEM STATUS"
line): green when nothing is outstanding, orange with a numbered list
otherwise. The list is built only from signals this system actually
tracks - the same checks as `ghostcash health`, plus queue depth
(`publishing/queue.py`, already written with the dashboard in mind -
see its own docstring), pending product approvals, and recent job
failures. Section 41's own illustrative example, "Buffer authentication
expires in 4 days," is deliberately **not** implemented: Buffer's API
gives no way to learn a token's expiry, and faking that check would be
exactly the kind of placeholder functionality this project's rules
forbid.

`/system` and the Overview banner share `ghostcash.health.check_system`
with the `ghostcash health` CLI command itself (pulled out into its own
module this phase) - the dashboard's "nothing required" claim can never
drift from what the CLI reports, because it is the same function call.

Hitting any page before the database has ever been created/migrated
(i.e. before `ghostcash init`) returns a plain "Database not ready - run
`ghostcash init`" page (HTTP 503) instead of a raw traceback, via a
`sqlite3.Error` exception handler registered on the app.

### Health checks, queue depth, and backups

`ghostcash health` (section 36) does more than check that a secret is
*set* - for each configured credential it makes one real, cheap,
read-only call and reports whether the service is actually reachable:
Gemini's `GET models/{model}` (metadata only, no generation quota
spent), Buffer's `account` query, and Gumroad's `GET /sales`. It also
flags two conditions GitHub Actions can't see on its own: no job has
*ever* succeeded, and the most recent successful job is older than
`max_job_age_hours` (default 36h - a full day of slack around nightly's
once/day schedule before this fires).

`ghostcash queue` (section 22) reports the publishing queue's
days-of-runway against the configured emergency/alert thresholds. It
exists as its own command, separate from `ghostcash publish`, so a
GitHub Actions step can check it both before an emergency
generate+render pass (to decide whether to run one) and after (to
confirm the buffer actually recovered) - see `publishing.yml`. With
`$GITHUB_OUTPUT`/`$GITHUB_STEP_SUMMARY` set (true inside any workflow
step), it also writes step outputs and a Markdown summary line, so a
workflow can branch on `below_emergency`/`below_alert` without
re-parsing stdout. It exits non-zero only when the queue is below the
*alert* threshold - with no NotificationProvider built yet (section 38),
a failed scheduled run is the real, zero-setup alert GitHub already
gives you by default (a failure email/notification to the repo's
watchers), not a fake integration pretending to page someone.

`ghostcash backup` (section 58) takes a compressed, consistent snapshot
of the live database using SQLite's own online backup API - safe to run
against a database still being written to, unlike a plain file copy,
which can capture a half-written WAL-mode database - and prunes old
snapshots to "7 daily, 4 weekly." Backups are written outside git
(`data/backups/`, already in `.gitignore`) because `orders` records a
customer's email per sale (section 27); that must never land in a
public repository's permanent history. See "GitHub deployment" below
for how backups actually survive GitHub's ephemeral runners.

### Autonomous optimization

`ghostcash optimize` (sections 30-32, 50-51) runs the weekly loop the
spec describes as *collect data -> analyze -> identify winners ->
identify losers -> update category/hook/template weights -> generate
new content*:

- **Statistics** (`optimization/statistics.py`) aggregate every
  measured `content_performance` row (i.e. anything `ghostcash
  analytics` has already given a Winner Score) across all eight
  dimensions section 31 names - category, topic, hook type, template,
  CTA type, content length, posting hour, platform, product.
- **Learned weights** (`optimization/weights.py`) turn
  category/hook-type/template performance into multipliers
  (performance-proportional, clipped to `[0.5, 2.0]`, blended in with
  an exponential moving average so one noisy week can't whipsaw
  future selection), persisted in `settings` and read back as a soft
  prompt hint by `content/topics.py` and `content/generation.py` -
  "identify losers" has no separate step, because a poor performer's
  influence is continuously demoted rather than needing a hard cut.
  Nothing is ever demoted to silence: section 8's "do not manually
  assume a winning niche" means even a weak category still gets a
  normal share of ideas.
- **Winner replication** (`optimization/replication.py`, section 30)
  asks the AI for 5 alternative hooks, 5 alternative scripts, 3
  alternative CTAs, and 3 related topics for every `WINNER` not yet
  replicated, capped at `max_winners_replicated_per_run` AI calls per
  run (section 46 cost control - any remainder is picked up next run).
  One alternative hook is also promoted into a real new `content` row,
  identical to the winner apart from that one hook, paired with it as
  a controlled experiment.
- **Experiment evaluation** (`optimization/experiments.py`, section
  51) judges each hook A/B test by practical margin (this project's
  content volume is too low for real statistical significance to mean
  anything) once both arms clear `min_views_for_experiment_evaluation`,
  or closes it out `INCONCLUSIVE` after `experiment_timeout_days`. The
  `experiments` table doubles as section 51's "experiment log."

The whole cycle is self-gating: section 50 says "every week", so
`ghostcash optimize` checks when it last actually ran real work and
exits cleanly (`[skip]`, exit 0) until `optimization.interval_days`
(default 7) has passed - `--force` overrides this. That means
`nightly.yml` can call it every night with zero extra scheduling logic;
most nights it's a fast no-op.

## 3. Installation

Requirements: Python 3.12+, git, and **ffmpeg** (with libflite support -
see above). This environment already has all three; on a fresh machine:

```bash
sudo apt-get install -y ffmpeg fonts-dejavu-core
```

```bash
git clone <your-fork-url> ghostcash
cd ghostcash
./scripts/bootstrap.sh
```

`bootstrap.sh` creates a virtualenv, installs the package (`pip install
-e ".[dev]"`), runs `ghostcash init` (creates `.env` and
`config/config.yaml` from their templates, applies database migrations),
runs the test suite, and runs a health check. It's safe to re-run.

Manually, the equivalent is:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ghostcash init
pytest
ghostcash health
```

Once you've added a real `AI_API_KEY` to `.env` (see below), the
fastest way to see the whole pipeline work is one command - see
"Dry-run mode" below:

```bash
ghostcash run --dry-run
```

Or run each step individually:

```bash
ghostcash generate --count 20 --content-count 5
ghostcash render
ghostcash publish
ghostcash analytics
ghostcash products detect
ghostcash products list
ghostcash products approve <opportunity_id>
# ... create the product yourself at https://app.gumroad.com/products ...
ghostcash products link <opportunity_id> <gumroad_product_id>
ghostcash products sync-sales
ghostcash report              # plain-text summary of all of the above
ghostcash dashboard           # same summary, as a local web UI - open http://127.0.0.1:8000
ghostcash queue               # publishing queue depth vs. the configured buffer targets
ghostcash backup              # compressed database snapshot, pruned to the retention policy
ghostcash optimize            # update learned weights, replicate winners, evaluate experiments
```

## 4. Environment variables

See [`.env.example`](.env.example) for the full, current list. Copy it to
`.env` (or let `ghostcash init` do that for you) and fill in what you
need. Only `AI_API_KEY` is required to reach Phase 2; the rest can stay
blank until you reach the phase that needs them.

| Variable                  | Required for          | Notes                                    |
|----------------------------|------------------------|-------------------------------------------|
| `AI_PROVIDER`              | Phase 2+               | Defaults to `gemini`                      |
| `AI_MODEL`                 | Phase 2+               | Provider-specific model name              |
| `AI_API_KEY`               | Phase 2+               | **Secret** - never commit                 |
| `AI_MAX_REQUESTS_PER_RUN`  | Phase 2+               | Cost control (section 46)                 |
| `AI_TEMPERATURE`           | Phase 2+               |                                            |
| `BUFFER_API_KEY`           | Phase 6+ (publishing, analytics) | **Secret** - must be a *personal* API key for analytics (section 28) |
| `GITHUB_TOKEN`             | Phase 6+ (publishing)  | **Secret** - only for local runs; GitHub Actions provides one automatically |
| `GUMROAD_API_KEY`          | Phase 8+ (products)    | **Secret** - access token from Settings -> Advanced -> Applications (see below); read-only (listing products/sales) - the API cannot create or update products |
| `NOTIFICATION_PROVIDER`    | Optional               | `email` / `discord` / `telegram`          |
| `NOTIFICATION_TOKEN`       | Optional               | **Secret**                                |
| `DATABASE_PATH`            | Optional               | Defaults to `data/ghostcash.db`           |
| `GHOSTCASH_CONFIG_PATH`    | Optional               | Override which `config.yaml` is loaded    |

Non-secret tuning (scoring weights, budgets, approval modes, video
settings, queue thresholds) lives in `config/config.yaml`, not `.env` -
see [`config/config.example.yaml`](config/config.example.yaml).

## 5. External accounts

| Account                | Required?              | Cost                     | Needed from |
|-------------------------|-------------------------|---------------------------|--------------|
| GitHub                  | Required                | Free (public repo)        | Phase 1; also used for video hosting from Phase 6 (see "Publishing") |
| Google AI Studio (Gemini) | Required               | Free tier available       | Phase 2      |
| Buffer                  | Required for publishing/analytics | Free plan has API access  | Phase 6; analytics (Phase 7) needs a personal API key specifically |
| Gumroad                 | Required for products   | Free to list, takes a cut | Phase 8; generate an access token at Settings -> Advanced -> Applications -> Create application -> Generate access token (redirect URI "localhost" is fine). Products themselves must still be created by hand in the Gumroad dashboard - its public API doesn't support creating/updating products (see "Products" above) |
| Email / Discord / Telegram | Optional              | Free                       | Any time     |

The system is designed to run for roughly **€0-15/month** (CLAUDE.md
section 4), excluding transaction fees and any AI usage beyond free
allowances - see the `monthly_budget` block in `config/config.yaml`.

## 6. Local development

```bash
pytest                          # run tests
ruff check src tests            # lint
ruff format src tests           # auto-format
mypy src                        # type-check
```

CI-equivalent, all from a clean clone:

```bash
pip install -e ".[dev]" && pytest && ruff check src tests && mypy src
```

## 7. Dry-run mode

CLAUDE.md section 43 requires `ghostcash run --dry-run` to show what
*would* happen with zero external side effects, before real publishing
is ever enabled - and this is real now (Phase 12/section 62's own
Definition of Done):

```
$ ghostcash run --dry-run
GhostCash end-to-end run (DRY RUN)
========================================
...
Topics generated: 20
Topics selected: 5
Scripts generated: 5
Videos rendered: 5
Posts that WOULD be scheduled: 15
Products affected: 0
External API writes: DISABLED

GhostCash daily report
========================================
...
```

Every step through rendering runs for real against your configured AI
provider and local ffmpeg - topics are generated, scored, and persisted;
scripts are generated and pass through the same quality control and
duplicate detection as `ghostcash generate`; videos are actually
rendered to `assets/generated/`. Only the "publication schedule" step
is computed instead of executed: it's `(READY content) x (configured
platforms)`, a plain count with no network call, because dry-run must
never touch Buffer, GitHub Releases, or Gumroad. That's also why it
needs only `AI_API_KEY` - no `BUFFER_API_KEY` required - matching
section 63's "first live test" workflow, where a dry run comes before
Buffer is even set up. Analytics is skipped rather than faked, since
nothing was actually published this run for Buffer to have metrics on.

Drop `--dry-run` and `ghostcash run` performs the identical sequence
with real external integrations (section 62: "must perform the same
workflow with real external integrations") - real Buffer scheduling and
real analytics collection, wherever those credentials are configured.
Per section 52, Buffer posts are still created as **drafts** by default
(`human_approval_mode.publishing: APPROVAL_REQUIRED`), so "real" means
real API calls, not that anything reaches a live audience unattended.
A publishing- or analytics-specific problem (missing credentials, a
Buffer error) is reported and skipped rather than failing the whole
run - content generation and rendering having already succeeded
shouldn't be thrown away over it.

`--topics-count` / `--content-count` override `content.topics_per_run`
/ `content.content_per_run` for a single invocation, same as
`ghostcash generate`'s equivalent flags.

## 8. GitHub deployment

Four workflows live in `.github/workflows/`, all scheduled and all
doing real work:

| Workflow | Schedule | Does |
|---|---|---|
| `healthcheck.yml` | hourly | `ghostcash health` against the persisted database - real AI/Buffer/Gumroad connectivity checks, not just "is the secret set" |
| `nightly.yml` | once/day | health check -> backup -> queue check -> `ghostcash optimize` (weekly, self-gated) -> generate -> render -> publish -> detect product opportunities -> report -> final queue check (the alert) |
| `analytics.yml` | once/day, ~90 min after nightly | `ghostcash analytics` (Winner Score) + `ghostcash products sync-sales` |
| `publishing.yml` | every 4 hours | queue check -> emergency generate+render if critically low -> publish -> queue check again |

**Persistence (the question this used to be open):** GitHub-hosted
runners are ephemeral, so `data/ghostcash.db` doesn't exist on a fresh
one. Every workflow restores it (plus `data/backups/`) from a shared
GitHub Actions cache entry at the start of the job and saves it back at
the end (`if: always()`, so a mid-run failure still keeps that run's
progress), via `actions/cache/restore` + `actions/cache/save` with
`restore-keys` pulling the most recently saved entry. The database is
deliberately **not** committed to git - `orders.customer_email` is real
customer data (section 27) that must never enter a public repo's
permanent history, whereas a cache entry is scoped to the repo and not
world-readable the way a git blob would be. All four workflows share
one `concurrency.group` so GitHub Actions serializes them - SQLite is
single-writer, and nothing here takes an application-level lock across
separate runs otherwise.

Deliberately **not** wired into any workflow: `products approve`/
`products link` (section 24 requires a human in the loop for product
activation - a returning owner sees new `PENDING_APPROVAL` opportunities
via `ghostcash products list` and acts on them by hand), and `ghostcash
dashboard` (a foreground local web server for a human to open in a
browser, not something a CI runner can usefully start and leave
running).

To enable these on GitHub: push this repo, then add any secrets you
have under **Settings -> Secrets and variables -> Actions**
(`AI_API_KEY`, `BUFFER_API_KEY`, `GUMROAD_API_KEY`). Every step that
needs a secret checks for it first and skips cleanly if it's missing -
`secrets` can't be referenced directly inside a workflow `if:`
expression, so each workflow reads them into `env:` in one step and
branches later steps on that step's own output instead.

## 9. Troubleshooting

- **`ghostcash health` reports "database is missing migrations"** - run
  `ghostcash init` (safe to re-run).
- **`ConfigError: No configuration file found`** - copy
  `config/config.example.yaml` to `config/config.yaml`, or run
  `ghostcash init`.
- **`pip install` fails with "externally-managed-environment"** - you're
  not inside the virtualenv; run `source .venv/bin/activate` first (or
  use `.venv/bin/pip` / `.venv/bin/ghostcash` directly).
- **`ghostcash render` (or `health`) says ffmpeg lacks 'flite' support**
  - install the standard distro package rather than a minimal custom
    build: `sudo apt-get install -y ffmpeg fonts-dejavu-core`.
- **`ghostcash publish` says your Buffer account has multiple
  organizations** - set `publishing.organization_id` in `config.yaml`
  to the one you want (the error message lists the available IDs).
- **`ghostcash publish` can't attach a video** - `publishing.github_repo`
  must be set to `"owner/repo"`, and you need a `GITHUB_TOKEN` for local
  runs (GitHub Actions provides one automatically).
- **A post never appears on TikTok** - expected today; Buffer's public
  API doesn't support creating TikTok posts. See "Publishing" above.
- **`ghostcash dashboard` shows "Database not ready" (HTTP 503)** - the
  database hasn't been created/migrated yet; run `ghostcash init` first,
  then reload the page.
- **`ghostcash dashboard` says the port is already in use** - another
  process (maybe a previous dashboard run) is bound to it; pick a free
  one with `ghostcash dashboard --port 8001`.

## 10. How to stop the system

There's no running daemon to stop today beyond `ghostcash dashboard`
itself, which is foreground and stops with Ctrl+C (everything else is
one-shot CLI commands + scheduled GitHub Actions). Once workflows are
enabled: disable them from the repo's **Actions** tab, or delete the
`schedule:` triggers from the relevant `.yml` files and push. Nothing
here will re-enable itself.

## 11. How to reset the database

```bash
rm data/ghostcash.db
ghostcash init
```

This recreates an empty, fully-migrated database. If you have a backup
you want to keep (section 58 - see `ghostcash backup` under
"Architecture" above), restore from `data/backups/` instead of a hard
reset:

```bash
gunzip -c data/backups/ghostcash-<timestamp>.db.gz > data/ghostcash.db
```

## 12. How to add another AI provider

Implement the `AIProvider` interface (arriving in `src/ghostcash/ai/` in
Phase 2):

```python
class AIProvider:
    def generate(self, prompt: str, **kwargs) -> str: ...
    def structured_generate(self, prompt: str, schema: dict) -> dict: ...
```

Register it, then set `AI_PROVIDER` (env) or `ai.provider` (config.yaml)
to select it. Nothing else in the system should depend on a specific
provider (CLAUDE.md section 9).

## 13. How to add another publishing platform

Implement the `Publisher` interface (`src/ghostcash/publishing/base.py`):

```python
class Publisher(ABC):
    @abstractmethod
    def schedule(self, content, platform, *, video_url=None, save_to_draft=False):
        ...
```

`BufferPublisher` is the current implementation. To use a different
provider entirely (not Buffer), implement `Publisher`, add a branch to
`publishing/factory.py`'s `get_publisher()`, and point
`publishing.provider` at it in `config.yaml`.

To add a platform *within* Buffer, add its Buffer `service` name to
`SERVICE_TO_PLATFORM` in `publishing/buffer.py`, add the platform to
`platforms.name`'s `CHECK` constraint (a new migration), and to
`config.yaml`'s `platforms` list. A failure on one platform must never
block the others (section 21) - see how TikTok is handled today for the
pattern: Buffer's public API doesn't support posting to it, so
`BufferPublisher.schedule()` rejects it immediately with a clear
message, and `ghostcash publish` treats that exactly like any other
per-platform failure.

## 14. How to add a product

Products always require a human approval step (CLAUDE.md section 24):
the system may detect an opportunity and draft a proposal, but a person
approves it before it goes live. As implemented in Phase 8:

1. `ghostcash products detect` writes a `PENDING_APPROVAL` row to
   `product_opportunities` with a proposed name, price recommendation,
   and sales copy, for any topic with enough views and no product yet
   (see "Products" under Architecture for the exact thresholds).
2. You review it with `ghostcash products list` and run
   `ghostcash products approve <id>` or `products reject <id>`.
3. **Because Gumroad's public API cannot create or update products**
   (verified - see "Products" above), you create the actual product
   yourself in the Gumroad dashboard at
   <https://app.gumroad.com/products>, using the opportunity's proposed
   name/price/sales copy as a starting point.
4. Run `ghostcash products link <opportunity_id> <gumroad_product_id>`.
   This confirms the product really exists via Gumroad's `GET
   /products/:id`, creates the local `products` row
   (`status = 'ACTIVE'`), and attaches the topic's content to it so
   future sales can be attributed back to it.
5. `ghostcash products sync-sales` (run nightly in CI, or by hand) then
   pulls real sales from Gumroad into `orders` and attributes revenue to
   the content that's been promoting the product.

To add a store other than Gumroad, implement the `Store` abstract base
class (`src/ghostcash/products/base.py`: `create_product`,
`update_product`, `get_product`, `get_sales`), add a branch to
`products/factory.py`'s `get_store()`, and point `products.store_provider`
at it in `config.yaml`.

You can also add a product manually at any time by inserting directly
into the `products` table with `status = 'ACTIVE'`.

## Roadmap

- [x] Phase 1 - Repository + configuration + database
- [x] Phase 2 - AI provider + topic generation
- [x] Phase 3 - Content generation + quality control
- [x] Phase 4 - Video renderer
- [x] Phase 5 - CLI (delivered incrementally with each phase, not as a separate step)
- [x] Phase 6 - Buffer integration
- [x] Phase 7 - Analytics
- [x] Phase 8 - Product abstraction
- [x] Phase 9 - Dashboard
- [x] Phase 10 - GitHub Actions (full pipeline)
- [x] Phase 11 - Autonomous optimization
- [x] Phase 12 - Full end-to-end test (`ghostcash run` / `run --dry-run`)

All 12 phases of CLAUDE.md section 61's build order are now implemented
and tested - see "MVP complete" at the top of this README for what that
does and doesn't mean in practice. See [`CLAUDE.md`](./CLAUDE.md)
section 61 for the authoritative build order and section 62 for the MVP
definition of done.

## License

[MIT](./LICENSE).
