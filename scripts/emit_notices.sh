#!/usr/bin/env bash
# Show a text file on a GitHub Actions run page:
#
#   scripts/emit_notices.sh "<title>" <file>
#
# Appends the file to the job summary (readable in the GitHub app) and also
# emits it as "notice" annotations, 40 lines each, which the GitHub API can
# read back (job logs are not always reachable).
set -u
title="$1"; file="$2"
[ -s "$file" ] || { echo "(nothing to show for $title)"; exit 0; }

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    { echo "### $title"; echo '```'; cat "$file"; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
fi

parts=$(mktemp -d)
split -l 40 "$file" "$parts/part_"
n=0
for part in "$parts"/part_*; do
    n=$((n + 1))
    message=$(sed 's/%/%25/g; s/\r//g' "$part" | awk '{printf "%s%%0A", $0}')
    echo "::notice title=$title ($n)::$message"
done
rm -rf "$parts"
