"""SQLite database layer for GhostCash (CLAUDE.md section 6).

No ORM, no external database dependency - just sqlite3 (stdlib) plus a
tiny hand-rolled migration runner. This keeps the system small, boring,
and dependency-free, per CLAUDE.md section 4 and 67.
"""

from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.database.migrate import apply_migrations, available_migrations, get_applied_versions

__all__ = [
    "apply_migrations",
    "available_migrations",
    "fail_job",
    "get_applied_versions",
    "get_connection",
    "start_job",
    "succeed_job",
]
