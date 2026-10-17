# GHOSTCASH

## Autonomous Faceless Content → Traffic → Digital Product Revenue System

**Version:** 1.0
**Status:** Build immediately
**Primary objective:** Create a low-cost autonomous digital revenue system requiring less than 30 minutes/week of human supervision after deployment.

---

# 1. ROLE

You are the lead software architect, developer, DevOps engineer and automation engineer responsible for implementing this system.

Do not merely describe the solution.

**Build it.**

Create the repository, source code, configuration, tests, documentation, workflows and deployment files required to make the system operational.

Work incrementally.

After completing each major subsystem:

1. Run tests.
2. Run lint/type checks.
3. Verify the implementation.
4. Fix failures.
5. Commit the working state.
6. Continue to the next subsystem.

Do not ask me to make architectural decisions that are already specified in this document.

If an implementation detail is unspecified, choose the simplest reliable solution that minimizes:

* recurring cost
* maintenance
* external dependencies
* API calls
* human intervention

---

# 2. BUSINESS OBJECTIVE

The system exists to generate revenue.

Views, followers, likes and subscribers are secondary metrics.

The primary KPI is:

**Revenue generated / human hours required**

The system should eventually operate as:

```text
RESEARCH
   ↓
IDEA GENERATION
   ↓
CONTENT GENERATION
   ↓
QUALITY CONTROL
   ↓
VIDEO GENERATION
   ↓
SOCIAL DISTRIBUTION
   ↓
TRAFFIC
   ↓
DIGITAL PRODUCT
   ↓
SALE
   ↓
AUTOMATIC DELIVERY
   ↓
ANALYTICS
   ↓
LEARNING
   ↓
BETTER CONTENT
   ↓
MORE SALES
```

The system must continuously improve based on observed performance.

---

# 3. OPERATING PRINCIPLE

The owner should NOT have to:

* write content
* edit videos
* manually schedule posts
* manually publish posts
* manually deliver products
* manually analyze every post
* manually create captions
* manually generate product descriptions
* manually monitor every automation

The owner should primarily:

* configure the system initially
* approve new products when necessary
* inspect the dashboard periodically
* resolve exceptional failures
* collect revenue

Target:

**<30 minutes/week after stabilization.**

---

# 4. TECHNOLOGY PRINCIPLES

Prefer:

* Python 3.12+
* SQLite
* standard Python libraries where practical
* FFmpeg
* Git
* GitHub
* GitHub Actions
* environment variables
* REST/GraphQL APIs
* deterministic templates
* open-source libraries

Avoid unnecessary:

* SaaS subscriptions
* paid automation platforms
* cloud databases
* Kubernetes
* Docker unless genuinely useful
* microservices
* complicated frontend frameworks
* proprietary infrastructure

The initial architecture must be capable of operating for approximately:

**€0–€15/month**

excluding transaction fees and optional AI/API usage beyond free allowances.

---

# 5. REPOSITORY STRUCTURE

Create:

```text
ghostcash/
│
├── CLAUDE.md
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
│
├── config/
│   ├── config.example.yaml
│   └── content_categories.yaml
│
├── data/
│   ├── ghostcash.db
│   └── seed/
│
├── src/
│   └── ghostcash/
│       ├── __init__.py
│       │
│       ├── cli/
│       │
│       ├── config/
│       │
│       ├── database/
│       │
│       ├── models/
│       │
│       ├── ai/
│       │
│       ├── research/
│       │
│       ├── content/
│       │
│       ├── scoring/
│       │
│       ├── video/
│       │
│       ├── publishing/
│       │
│       ├── products/
│       │
│       ├── analytics/
│       │
│       ├── optimization/
│       │
│       ├── notifications/
│       │
│       └── dashboard/
│
├── templates/
│   ├── video/
│   ├── captions/
│   ├── products/
│   └── emails/
│
├── assets/
│
├── tests/
│
├── scripts/
│
└── .github/
    └── workflows/
        ├── nightly.yml
        ├── publishing.yml
        ├── analytics.yml
        └── healthcheck.yml
```

---

# 6. DATABASE

Use SQLite.

Do NOT introduce PostgreSQL or another database for MVP.

Create migrations.

Required tables:

```text
content
content_variants
content_performance
topics
topic_scores
products
product_opportunities
campaigns
platforms
published_posts
analytics_snapshots
jobs
job_errors
system_events
settings
```

---

# 7. CONTENT MODEL

Each content item must contain:

```text
id
topic
category
title
hook
script
caption
cta
product_id
content_type
status
quality_score
opportunity_score
created_at
scheduled_at
published_at
```

Supported statuses:

```text
IDEA
RESEARCHING
DRAFT
APPROVED
RENDERING
READY
SCHEDULED
PUBLISHED
ANALYZING
WINNER
REJECTED
FAILED
```

---

# 8. INITIAL CONTENT CATEGORIES

Do NOT lock the system into one niche.

Start with:

```text
AI
AUTOMATION
SOFTWARE
WINDOWS
EXCEL
PRODUCTIVITY
MONEY_SAVING
BUSINESS
CAREER
INTERNET_TOOLS
PSYCHOLOGY
TECHNOLOGY
INTERESTING_FACTS
LIFE_HACKS
```

The system must be able to discover that certain categories outperform others.

Do not manually assume a winning niche.

Let the data decide.

---

# 9. AI ABSTRACTION

Create an AI provider interface:

```python
class AIProvider:
    def generate(self, prompt: str, **kwargs) -> str:
        ...

    def structured_generate(self, prompt: str, schema: dict) -> dict:
        ...
```

Implement:

```text
GeminiProvider
```

Do not hard-code the rest of the system to Gemini.

The system must support future providers:

```text
OpenAI
Anthropic
Ollama
other compatible provider
```

through the same interface.

---

# 10. AI CONFIGURATION

All AI configuration must come from environment/config:

```text
AI_PROVIDER
AI_MODEL
AI_API_KEY
AI_MAX_REQUESTS_PER_RUN
AI_TEMPERATURE
```

Never commit API keys.

---

# 11. TOPIC GENERATION

Create a scheduled process:

```text
generate_topics
```

It must generate at least:

**20 candidate topics per run**

unless the configured quota is lower.

Each topic must contain:

```text
topic
category
hook
target_audience
evergreen_score
curiosity_score
shareability_score
commercial_score
production_cost_score
competition_score
overall_score
```

---

# 12. CONTENT SCORING

Implement:

```text
Opportunity Score =
  0.20 curiosity
+ 0.20 shareability
+ 0.20 commercial relevance
+ 0.15 evergreen value
+ 0.15 audience usefulness
+ 0.10 production simplicity
- competition penalty
```

Make weights configurable.

Do not hard-code them permanently.

---

# 13. CONTENT GENERATION

For selected topics generate:

```text
Hook
Script
Caption
CTA
Suggested visual sequence
Suggested product
Alternative hook
```

Default short-form duration:

**15–35 seconds**

Target:

**one idea per video**

Avoid:

* generic motivational content
* obvious AI filler
* repetitive wording
* unsupported claims
* fake statistics
* fabricated quotations
* copied content

---

# 14. QUALITY CONTROL

Before content becomes publishable, run automated validation.

Reject content if:

* script is empty
* hook is missing
* CTA is missing
* excessive repetition exists
* unsupported factual claims are detected
* content is too similar to existing content
* script exceeds configured length
* product link is invalid
* rendering assets are missing

Generate:

```text
quality_score: 0–100
```

Only publish if:

```text
quality_score >= configurable threshold
```

Default:

**80**

---

# 15. DUPLICATE DETECTION

Implement semantic-ish duplicate detection using a cheap local mechanism first.

Requirements:

* compare new scripts against recent scripts
* detect high textual similarity
* reject near duplicates
* allow controlled variations of proven winners

Store a content fingerprint.

---

# 16. VIDEO ENGINE

Do NOT initially use expensive AI video generation.

Build videos using:

```text
Python
+
FFmpeg
+
text overlays
+
images
+
simple motion
+
TTS
+
subtitles
```

Create reusable templates.

Initial format:

```text
1080x1920
9:16
H.264 MP4
```

The renderer must accept:

```text
script
background
visual assets
voice audio
captions
CTA
```

and output:

```text
final.mp4
```

---

# 17. VIDEO TEMPLATE

Create at least three templates.

### Template A — Information

```text
HOOK
↓
POINT 1
↓
POINT 2
↓
POINT 3
↓
CTA
```

### Template B — List

```text
"3 things..."
↓
1
↓
2
↓
3
↓
CTA
```

### Template C — Problem/Solution

```text
PROBLEM
↓
WHY IT HAPPENS
↓
SOLUTION
↓
CTA
```

Templates must be configurable without changing Python code.

---

# 18. TTS

Implement a TTS abstraction:

```python
class TTSProvider:
    def synthesize(self, text: str, output_path: str):
        ...
```

First implementation should prioritize:

**free/local TTS**

if technically viable.

Allow future API TTS providers.

---

# 19. PUBLISHING

Implement a publisher abstraction:

```python
class Publisher:
    def schedule(self, content):
        ...
```

Initial implementation:

```text
BufferPublisher
```

Buffer's current API is GraphQL and supports creating/scheduling posts, retrieving channels, retrieving posts and related account data. API access is available on the Free plan. Use the official API documentation rather than reverse engineering Buffer.

Buffer API endpoint:

```text
https://api.buffer.com
```

Authentication must use an environment variable.

```text
BUFFER_API_KEY
```

Never store the token in source code.

---

# 20. BUFFER INTEGRATION

Implement:

```text
get_account()
get_organizations()
get_channels()
create_post()
get_scheduled_posts()
get_sent_posts()
delete_post()
```

Use the current official Buffer GraphQL schema.

Do not invent GraphQL mutations.

Before implementing the integration, inspect the current official Buffer API documentation.

---

# 21. INITIAL CHANNELS

Support:

```text
YouTube
TikTok
Instagram
```

But make channel selection configuration-driven.

The system must continue operating if one channel fails.

Example:

```text
TikTok failure
     ↓
log error
     ↓
retry
     ↓
continue publishing to YouTube/Instagram
```

---

# 22. PUBLISHING QUEUE

Maintain a minimum content buffer.

Default:

```text
7 days
```

If queue falls below:

```text
3 days
```

trigger emergency content generation.

If queue falls below:

```text
1 day
```

send an alert.

---

# 23. PRODUCT SYSTEM

Create a product abstraction:

```python
class Product:
    id
    name
    description
    price
    url
    category
    status
```

Products must be externalized from code.

---

# 24. INITIAL PRODUCTS

Do NOT automatically publish paid products without human approval.

The system may:

```text
discover opportunity
↓
generate product proposal
↓
notify owner
↓
owner approves
↓
system publishes/activates product
```

This is one of the few intentional human gates.

Reason:

The system may generate ideas autonomously, but the owner should approve what is actually sold.

---

# 25. PRODUCT OPPORTUNITY DETECTION

The system must identify:

```text
high views
+
high clicks
+
low product coverage
```

as a potential product opportunity.

Example:

```text
Topic:
Excel automation

Views:
70,000

Clicks:
4,000

Existing product:
none

→ PRODUCT OPPORTUNITY
```

Generate:

```text
product name
problem solved
contents
price recommendation
target customer
sales page copy
```

---

# 26. PRODUCT STORE

Create a store adapter:

```python
class Store:
    def create_product(...)
    def update_product(...)
    def get_sales(...)
    def get_product(...)
```

Initial target:

**Gumroad**

Use the official Gumroad API/documentation when implementing it.

Do not scrape Gumroad.

Do not embed credentials.

---

# 27. PRODUCT DELIVERY

Products must be downloadable immediately after successful payment.

No manual fulfillment.

The system must record:

```text
order
customer
product
amount
currency
timestamp
status
```

---

# 28. ANALYTICS

Analytics must focus on revenue.

Collect where APIs permit:

```text
views
likes
comments
shares
clicks
profile visits
sales
revenue
```

Normalize metrics across platforms.

Calculate:

```text
views
engagement_rate
click_rate
conversion_rate
revenue_per_view
revenue_per_1000_views
revenue_per_content_item
```

---

# 29. WINNER DETECTION

A winner is NOT simply a video with the highest views.

Calculate:

```text
Winner Score =
  0.25 engagement
+ 0.25 click-through
+ 0.35 revenue
+ 0.15 retention/other available quality metrics
```

If revenue data is unavailable, redistribute weights automatically.

---

# 30. WINNER REPLICATION

For every winner:

Generate:

```text
5 alternative hooks
5 alternative scripts
3 alternative CTAs
3 related topics
```

Do NOT simply duplicate the original.

The objective is:

```text
WINNER
 ↓
PATTERN
 ↓
VARIATIONS
 ↓
TEST
 ↓
NEW WINNER
```

---

# 31. CONTENT LEARNING

The system must maintain statistics by:

```text
category
topic
hook type
video template
CTA
content length
posting time
platform
product
```

Use these statistics to improve future content selection.

---

# 32. AUTONOMOUS DECISION RULES

The system may automatically:

* generate ideas
* generate scripts
* reject poor content
* render videos
* schedule content
* reschedule failed posts
* analyze performance
* identify winners
* generate variations
* recommend products

The system must NOT automatically:

* spend money on advertising
* purchase subscriptions
* create financial transactions
* publish a new paid product without approval
* change account credentials
* delete an entire social account
* alter security settings

---

# 33. NIGHTLY WORKFLOW

Create:

```text
.github/workflows/nightly.yml
```

Run once per day.

Workflow:

```text
1. health check
2. database backup
3. retrieve current queue
4. retrieve analytics
5. analyze winners
6. generate topics
7. score topics
8. generate content
9. quality control
10. render videos
11. schedule content
12. detect product opportunities
13. generate daily report
14. send alerts if required
```

---

# 34. PUBLISHING WORKFLOW

Create:

```text
.github/workflows/publishing.yml
```

Run at configurable intervals.

Purpose:

```text
check queue
↓
publish/schedule missing content
↓
retry failures
↓
maintain buffer
```

---

# 35. ANALYTICS WORKFLOW

Create:

```text
.github/workflows/analytics.yml
```

Collect available metrics daily.

Do not make excessive API calls.

Use caching.

Respect platform rate limits.

---

# 36. HEALTH CHECK

Create:

```text
.github/workflows/healthcheck.yml
```

Verify:

```text
database accessible
AI API accessible
Buffer API accessible
store API accessible
required environment variables present
disk space sufficient
queue healthy
last successful job recent
```

---

# 37. FAILURE MANAGEMENT

Every job must have:

```text
job_id
started_at
finished_at
status
attempt
error
stack_trace
```

Implement exponential retry:

```text
30 sec
2 min
10 min
```

Maximum:

**3 retries**

After final failure:

```text
FAILED
↓
log
↓
continue unrelated jobs
↓
notify owner
```

---

# 38. NOTIFICATIONS

Start with the cheapest reliable notification method.

Implement:

```text
NotificationProvider
```

Allow:

```text
Email
Discord
Telegram
```

The first implementation may use email or Telegram.

Send notifications only for:

```text
critical failures
authentication expiration
publishing failures
product approval required
system stopped
unusually high revenue
```

Do NOT spam the owner.

---

# 39. DASHBOARD

Build a lightweight local dashboard.

Do not build a large frontend.

Use:

```text
FastAPI
+
simple HTML
```

or an equally lightweight solution.

Dashboard pages:

```text
/
Overview

/content
Content

/products
Products

/analytics
Analytics

/jobs
Jobs

/system
System health
```

---

# 40. MAIN DASHBOARD

Display:

```text
Revenue today
Revenue this week
Revenue this month

Orders

Content published

Content queued

Top 10 content

Top categories

Top products

Failed jobs

System health
```

Use simple tables and charts.

---

# 41. OWNER VIEW

The dashboard should answer one question:

> "Do I need to do anything?"

At the top:

```text
SYSTEM STATUS

🟢 RUNNING — NOTHING REQUIRED
```

or:

```text
🟠 ATTENTION REQUIRED

1. Buffer authentication expires in 4 days.
2. Product approval required.
```

---

# 42. CLI

Create:

```bash
ghostcash init
ghostcash health
ghostcash generate
ghostcash render
ghostcash publish
ghostcash analytics
ghostcash optimize
ghostcash report
ghostcash run
```

And:

```bash
ghostcash run --dry-run
```

The dry-run mode is mandatory.

---

# 43. DRY RUN

Before any external publishing:

```bash
ghostcash run --dry-run
```

must show:

```text
Topics generated: 20
Topics selected: 5
Scripts generated: 5
Videos rendered: 5
Posts that WOULD be scheduled: 15
Products affected: 0
External API writes: DISABLED
```

No external mutation is allowed in dry-run mode.

---

# 44. TESTING

Implement unit tests for:

* database
* scoring
* content validation
* duplicate detection
* video rendering
* configuration
* Buffer integration
* product integration
* analytics
* winner detection
* retries
* queue management

External APIs must be mocked in unit tests.

Create integration tests separately.

---

# 45. SECURITY

Never commit:

```text
API keys
tokens
passwords
cookies
session data
```

Use:

```text
.env
GitHub Secrets
```

Validate environment variables at startup.

Fail clearly if required secrets are missing.

---

# 46. COST CONTROL

Implement a monthly usage tracker.

Track:

```text
AI calls
AI estimated cost
API calls
video renders
external requests
```

Configuration:

```yaml
monthly_budget:
  ai: 5
  video: 0
  automation: 0
  total: 15
```

The system must stop expensive optional operations when the configured budget is exceeded.

Free/local operations should continue.

---

# 47. CONTENT SAFETY / QUALITY

The system should prioritize:

* original content
* useful information
* commentary
* explanations
* transformations
* tutorials
* genuinely useful tools

Avoid automated plagiarism.

Do not scrape and republish other creators' scripts.

Do not fabricate sources.

Do not generate fake testimonials.

Do not generate fake statistics.

Do not impersonate real people.

Do not create deceptive engagement.

---

# 48. RESEARCH

Create a research abstraction:

```python
class ResearchProvider:
    def search(self, query: str):
        ...
```

The research engine must be pluggable.

Do not make the entire system dependent on one search provider.

For MVP, research can operate from:

* permitted APIs
* RSS
* public webpages where appropriate
* curated sources
* existing knowledge

Research should primarily support topic discovery and factual validation.

---

# 49. CONTENT FRESHNESS

Each topic must be classified:

```text
EVERGREEN
TRENDING
TIME_SENSITIVE
```

Time-sensitive content must have an expiration date.

Expired content must never be automatically published.

---

# 50. AUTONOMOUS OPTIMIZATION LOOP

Every week:

```text
COLLECT DATA
     ↓
ANALYZE
     ↓
IDENTIFY WINNERS
     ↓
IDENTIFY LOSERS
     ↓
UPDATE CATEGORY WEIGHTS
     ↓
UPDATE HOOK WEIGHTS
     ↓
UPDATE TEMPLATE WEIGHTS
     ↓
GENERATE NEW CONTENT
```

Maintain an experiment log.

---

# 51. EXPERIMENTATION

Every experimental batch must have:

```text
experiment_id
hypothesis
variable
control
variant
metrics
result
decision
```

Example:

```text
Hypothesis:
Question hooks produce higher click-through.

Control:
"This Excel trick saves time."

Variant:
"Why are you still doing this manually in Excel?"

Metric:
CTR

Decision:
Use question hooks if statistically/usefully better.
```

---

# 52. HUMAN APPROVAL MODE

The system must support:

```text
AUTONOMOUS
```

and:

```text
APPROVAL_REQUIRED
```

Modes.

Initial deployment:

```text
CONTENT = autonomous
PUBLISHING = approval_required
PRODUCTS = approval_required
```

After testing:

```text
CONTENT = autonomous
PUBLISHING = autonomous
PRODUCTS = approval_required
```

Never remove product approval automatically.

---

# 53. BOOTSTRAP PROCESS

Create:

```bash
./scripts/bootstrap.sh
```

It must:

1. check Python
2. create virtual environment
3. install dependencies
4. create database
5. apply migrations
6. create `.env` from example if missing
7. run tests
8. run health check
9. print remaining manual setup steps

---

# 54. MANUAL SETUP

The README must contain a precise checklist.

Expected external accounts:

```text
GitHub
Google/Gemini API
Buffer
Gumroad
optional email provider
optional notification provider
```

The system must clearly distinguish:

```text
REQUIRED
OPTIONAL
PAID
FREE
```

---

# 55. SECRETS

Create:

```text
.env.example
```

with:

```text
AI_PROVIDER=gemini
AI_API_KEY=

BUFFER_API_KEY=

GUMROAD_API_KEY=

NOTIFICATION_PROVIDER=
NOTIFICATION_TOKEN=

DATABASE_PATH=data/ghostcash.db
```

Never populate secrets.

---

# 56. GITHUB ACTIONS

Use GitHub Actions as the scheduler.

Use scheduled workflows.

Do not run a permanent server for MVP unless required.

Scheduled jobs must be idempotent.

A job running twice must not publish duplicate content.

Use database locking/idempotency keys.

---

# 57. IDEMPOTENCY

Every external operation must have an idempotency mechanism.

Examples:

```text
content_id
publication_id
product_id
job_id
```

Before publishing:

```text
Does this content already have a successful publication record?

YES → skip.

NO → publish.
```

---

# 58. BACKUPS

Before every major mutation:

```text
SQLite backup
```

Retain:

```text
7 daily backups
4 weekly backups
```

Compress backups.

---

# 59. LOGGING

Use structured logging.

Example:

```json
{
  "timestamp": "...",
  "level": "INFO",
  "component": "publisher",
  "event": "post_scheduled",
  "content_id": "abc123",
  "platform": "youtube"
}
```

---

# 60. README

The README must explain:

1. What GhostCash does.
2. Architecture.
3. Installation.
4. Environment variables.
5. External accounts.
6. Local development.
7. Dry-run mode.
8. GitHub deployment.
9. Troubleshooting.
10. How to stop the system.
11. How to reset the database.
12. How to add another AI provider.
13. How to add another publishing platform.
14. How to add a product.

---

# 61. IMPLEMENTATION ORDER

Do NOT attempt everything simultaneously.

Build in this order:

### Phase 1

Repository + configuration + database.

### Phase 2

AI provider + topic generation.

### Phase 3

Content generation + quality control.

### Phase 4

Video renderer.

### Phase 5

CLI.

### Phase 6

Buffer integration.

### Phase 7

Analytics.

### Phase 8

Product abstraction.

### Phase 9

Dashboard.

### Phase 10

GitHub Actions.

### Phase 11

Autonomous optimization.

### Phase 12

Full end-to-end test.

---

# 62. DEFINITION OF DONE

The MVP is NOT complete until this works:

```bash
ghostcash run --dry-run
```

and produces:

```text
20 candidate topics
        ↓
5 selected topics
        ↓
5 scripts
        ↓
5 videos
        ↓
5 validated posts
        ↓
publication schedule
        ↓
analytics records
        ↓
report
```

Then:

```bash
ghostcash run
```

must perform the same workflow with real external integrations.

---

# 63. FIRST LIVE TEST

Before autonomous publishing:

Generate:

**10 pieces of content**

Render them.

Create Buffer drafts rather than publishing.

Inspect:

* formatting
* captions
* spelling
* CTA
* video quality
* links

Only after validation should autonomous publishing be enabled.

---

# 64. FIRST PRODUCTION TARGET

Once the system passes the draft test:

```text
5 posts/day
```

across configured platforms.

Do NOT immediately maximize volume.

Run for:

**14 days**

and collect performance data.

Then allow the optimizer to determine whether volume should increase.

---

# 65. SCALING RULE

Never scale simply because content volume is possible.

Scale when:

```text
Revenue/content
or
Revenue/1000 views
```

improves.

The system should optimize for economic performance rather than vanity metrics.

---

# 66. LONG-TERM ARCHITECTURE

Eventually support:

```text
multiple content engines
multiple product families
multiple social accounts
multiple languages
multiple AI providers
multiple stores
```

but DO NOT implement those in MVP.

Design interfaces so they can be added later.

---

# 67. IMPORTANT ENGINEERING RULE

Do not build a complicated system merely because you can.

The ideal architecture is:

```text
SMALL
BORING
RELIABLE
CHEAP
AUTOMATED
OBSERVABLE
```

A Python script that runs every night and makes money is better than a beautiful Kubernetes cluster that requires maintenance.

---

# 68. YOUR FIRST RESPONSE TO ME

When you begin implementation, do NOT immediately dump thousands of lines of code.

First:

1. Inspect the environment.
2. Inspect the repository.
3. Determine available tools.
4. Check Python/FFmpeg/Git.
5. Create the project structure.
6. Create CLAUDE.md.
7. Create the database layer.
8. Create tests.
9. Run tests.
10. Report exactly what was created.
11. Continue implementation unless blocked by a credential/account requirement.

When external credentials are required, create the integration and a clear setup instruction, then continue implementing everything that does not require the credential.

Do not stop the entire project merely because one external API key is missing.

---

# 69. CLAUDE CODE OPERATING INSTRUCTION

Treat this document as the authoritative product specification.

If a requirement conflicts with a later explicit instruction from the project owner, the owner's explicit instruction wins.

Prefer implementation over explanation.

Do not repeatedly ask for confirmation.

Do not rewrite working components unnecessarily.

Do not add dependencies without justification.

Do not introduce recurring paid services without explicit approval.

Every feature must have tests.

Every external integration must have a mock.

Every scheduled operation must be idempotent.

Every failure must be observable.

Every expensive operation must have a configurable budget.

The final system must be capable of running unattended.

---

# 70. FINAL SUCCESS CONDITION

The finished system should allow the owner to do this:

```text
Monday:
check dashboard

Tuesday:
do nothing

Wednesday:
do nothing

Thursday:
do nothing

Friday:
check dashboard

Weekend:
do nothing
```

while the system continuously:

```text
researches
creates
renders
publishes
measures
learns
optimizes
sells
delivers
reports
```

The owner's role is:

# OPERATOR

not:

# CONTENT CREATOR

not:

# SOCIAL MEDIA MANAGER

not:

# VIDEO EDITOR

not:

# CUSTOMER FULFILLMENT WORKER

The machine does the repetitive work.

The owner handles only exceptions, strategic decisions and revenue.
