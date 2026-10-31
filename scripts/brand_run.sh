#!/usr/bin/env bash
# Run one extra channel ("brand") inside a GitHub workflow.
#
#   scripts/brand_run.sh <nightly|publishing|analytics>
#
# The workflow step sets GHOSTCASH_CONFIG_PATH to the brand's config file
# (for example config/brands/oldwisdom.yaml), plus AI_API_KEY, the brand's own
# Buffer key variable and GITHUB_TOKEN. Every `ghostcash` command below then
# works on that brand's own database, videos and Buffer account.
#
# A failing command is reported as a warning and the remaining commands still
# run; the script exits 1 at the end if anything failed, so the step shows a
# warning (the workflow marks it continue-on-error: the main channel's run is
# never stopped by a problem with an extra channel).

set -u

mode="${1:-}"
config="${GHOSTCASH_CONFIG_PATH:?GHOSTCASH_CONFIG_PATH must name the brand config file}"

# The brand's Buffer key variable is named inside its config file.
key_env=$(python - "$config" <<'PY'
import sys
from ghostcash.config.loader import load_config
print(load_config(sys.argv[1], load_env_file=False).brand.buffer_api_key_env)
PY
)
has_ai_key=false; [ -n "${AI_API_KEY:-}" ] && has_ai_key=true
has_buffer_key=false; [ -n "${!key_env:-}" ] && has_buffer_key=true

failed=0
step() {
    local label="$1"; shift
    echo "::group::$label"
    if ! "$@"; then
        echo "::warning title=Extra channel ($config)::$label failed - see the log above"
        failed=1
    fi
    echo "::endgroup::"
}

queue_reading() {
    # `ghostcash queue` writes its reading to $GITHUB_OUTPUT; keep this run's
    # reading in a file of its own so it does not mix with the main channel's.
    local out; out=$(mktemp)
    GITHUB_OUTPUT="$out" ghostcash queue
    local code=$?
    below_target=$(sed -n 's/^below_target=//p' "$out" | tail -1)
    rm -f "$out"
    return $code
}

echo "Channel config: $config (Buffer key variable: $key_env, set: $has_buffer_key)"

# Creates the brand's database on its first run; always cheap.
step "Prepare database" ghostcash init

case "$mode" in
    nightly)
        # Informational: a brand-new channel has no successful job yet, which
        # `health` reports as an issue. Never counted as a failure.
        echo "::group::Health check (informational)"; ghostcash health || true; echo "::endgroup::"
        step "Database backup" ghostcash backup
        below_target=""
        echo "::group::Check publishing queue"; queue_reading || true; echo "::endgroup::"
        if [ "$has_ai_key" = true ]; then
            if [ "$below_target" = "false" ]; then
                echo "[skip] The queue already holds enough videos, so no new videos are written tonight."
            else
                step "Generate topics and scripts" ghostcash generate
            fi
            step "Render videos" ghostcash render
        else
            echo "[skip] AI_API_KEY is not set - no generation or rendering."
        fi
        if [ "$has_buffer_key" = true ]; then
            step "Publish to Buffer" ghostcash publish
        else
            echo "[skip] $key_env is not set - nothing is sent to Buffer."
        fi
        step "Daily report" ghostcash report
        echo "::group::Final queue check (informational)"; queue_reading || true; echo "::endgroup::"
        ;;
    publishing)
        if [ "$has_buffer_key" = true ]; then
            # Remakes videos whose files vanished with the previous runner.
            if [ "$has_ai_key" = true ]; then
                step "Re-render missing videos" ghostcash render
            fi
            step "Publish to Buffer" ghostcash publish
        else
            echo "[skip] $key_env is not set - nothing to publish."
        fi
        ;;
    analytics)
        if [ "$has_buffer_key" = true ]; then
            step "Collect analytics" ghostcash analytics
        else
            echo "[skip] $key_env is not set - no analytics to collect."
        fi
        ;;
    *)
        echo "Usage: $0 <nightly|publishing|analytics>" >&2
        exit 2
        ;;
esac

exit $failed
