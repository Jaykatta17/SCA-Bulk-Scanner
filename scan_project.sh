#!/bin/bash
# SPDX-License-Identifier: Apache-2.0

# ============================================
# SCA Scanner Script - Bulk Scan from CSV
# ============================================

# === Base Directories ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR"
REPORT_DIR="$BASE_DIR/reports"
TEMPLATE_PATH="$BASE_DIR/template/html.tmpl"
SCAN_DATE=$(date +%F)
GRYPE_IMG="anchore/grype:latest"
SYFT_IMG="anchore/syft:latest"

# === Create Reports Directory ===
if [ ! -d "$REPORT_DIR" ]; then
    mkdir -p "$REPORT_DIR"
fi

# === Cleanup Previous Clones ===
if [ -d "$BASE_DIR/cloned_projects" ]; then
    echo "🧹 Cleaning up previous cloned projects..."
    rm -rf "$BASE_DIR/cloned_projects"
fi

echo "============================================================"
echo "🚀 SCA Scanner - Bulk Scan Automation"
echo "============================================================"
echo "📁 Base directory: $BASE_DIR"
echo "📊 Reports will be saved in: $REPORT_DIR"
echo "📄 Using template: $TEMPLATE_PATH"
echo "============================================================"

# === Temporary Template ===
TEMP_TEMPLATE="/tmp/html-injected.tmpl"
trap 'rm -f "$TEMP_TEMPLATE"' EXIT

# === Helpers ===
format_time() {
    local T=$1
    local M=$((T / 60))
    local S=$((T % 60))
    if [ $M -gt 0 ]; then
        printf "%dm %ds" "$M" "$S"
    else
        printf "%ds" "$S"
    fi
}

# Escape a value for safe use as a sed replacement (with '|' as delimiter):
# backslashes, the delimiter itself, and '&' (which sed expands to the
# matched text) all need escaping so project/branch names can't corrupt the
# substitution or the generated report.
escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\&|]/\\&/g'
}

# === Step 1: Scan projects in parallel from CSV ===
echo ""
echo "🔍 STEP 1: Processing projects in parallel"
echo "============================================================"

# Configuration for Parallelism & Resources
MAX_PARALLEL=6
ACTIVE_JOBS=0
START_TIME=$SECONDS

# Size per-container CPU/memory limits from the host's actual capacity so
# MAX_PARALLEL concurrent containers can't oversubscribe the machine.
HOST_CPUS=$(nproc 2>/dev/null || echo 4)
HOST_MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 8192)
CONTAINER_CPUS=$(awk -v cpus="$HOST_CPUS" -v jobs="$MAX_PARALLEL" 'BEGIN { v = cpus / jobs; if (v < 0.5) v = 0.5; printf "%.2f", v }')
CONTAINER_MEM_MB=$(awk -v mem="$HOST_MEM_MB" -v jobs="$MAX_PARALLEL" 'BEGIN { v = int(mem / jobs); if (v < 512) v = 512; print v }')
CONTAINER_MEM="${CONTAINER_MEM_MB}m"
echo "⚙️  Host: ${HOST_CPUS} CPUs, ${HOST_MEM_MB}m RAM -> ${MAX_PARALLEL} parallel jobs @ ${CONTAINER_CPUS} CPUs / ${CONTAINER_MEM} each"

# Get project info from CSV using the list flag
PROJECT_LIST=$(python3 "$BASE_DIR/clone_repos.py" --list)
TOTAL_PROJECTS=$(echo "$PROJECT_LIST" | grep -v "^$" | wc -l)

# Temporarily store results to aggregate later
RESULTS_DIR=$(mktemp -d)
trap 'rm -rf "$RESULTS_DIR"' EXIT

process_project() {
    local INDEX=$1
    local PROJECT_NAME=$2
    local DIR_NAME=$3
    local SCAN_ID=$4
    local JOB_START=$SECONDS
    
    echo "🚀 [$SCAN_ID of $TOTAL_PROJECTS] Starting: $PROJECT_NAME"
    
    # 📥 Clone the specific project by its index
    python3 "$BASE_DIR/clone_repos.py" --index-id "$INDEX" > /dev/null 2>&1
    
    if [ $? -ne 0 ]; then
        echo "   ❌ [$SCAN_ID] Cloning failed: $PROJECT_NAME"
        echo "fail" > "$RESULTS_DIR/$SCAN_ID"
        return
    fi

    local PROJECT_PATH="$BASE_DIR/cloned_projects/$DIR_NAME"
    
    # Enter project directory
    cd "$PROJECT_PATH" || return

    # === Check if it's a Git repo ===
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "   ⚠️  [$SCAN_ID] Not a Git repo: $PROJECT_NAME"
        echo "fail" > "$RESULTS_DIR/$SCAN_ID"
        cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
        return
    fi

    # === Get current branch and commit ID ===
    local BRANCH=$(git rev-parse --abbrev-ref HEAD)
    local SAFE_BRANCH=${BRANCH//\//_}
    local COMMIT=$(git rev-parse --short HEAD)

    # === Generate SBOM ===
    docker run --rm -e SYFT_CHECK_FOR_APP_UPDATE=false --name "SYFT-scanner-$SCAN_ID" \
        --cpus="$CONTAINER_CPUS" \
        --memory="$CONTAINER_MEM" \
        --memory-swap="$CONTAINER_MEM" \
        -v "$(pwd):/app" \
        $SYFT_IMG /app/ \
        -o cyclonedx-json > sbom.json 2>/dev/null

    if [ $? -ne 0 ]; then
        echo "   ❌ [$SCAN_ID] SBOM failed: $PROJECT_NAME"
        echo "fail" > "$RESULTS_DIR/$SCAN_ID"
        cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
        return
    fi

    # === Inject Variables into Template ===
    local TEMP_TEMPLATE_JOB="/tmp/html-injected-$SCAN_ID.tmpl"
    local ESCAPED_PROJECT_NAME=$(escape_sed_replacement "$PROJECT_NAME")
    local ESCAPED_BRANCH=$(escape_sed_replacement "$BRANCH")
    local ESCAPED_COMMIT=$(escape_sed_replacement "$COMMIT")
    sed \
        -e "s|PROJECT_VAR|$ESCAPED_PROJECT_NAME|g" \
        -e "s|BRANCH_VAR|$ESCAPED_BRANCH|g" \
        -e "s|COMMIT_VAR|$ESCAPED_COMMIT|g" \
        "$TEMPLATE_PATH" > "$TEMP_TEMPLATE_JOB"

    # === Run Grype Scan ===
    local SAFE_PROJECT_NAME=$(echo "$PROJECT_NAME" | sed 's/[^a-zA-Z0-9\-_]/_/g')
    local OUTPUT_FILE="$REPORT_DIR/SCA-${SAFE_PROJECT_NAME}-${SAFE_BRANCH}_${COMMIT}-${SCAN_DATE}.html"

    docker run --rm -e GRYPE_BY_CVE=true --name "grype-scanner-$SCAN_ID" \
        --cpus="$CONTAINER_CPUS" \
        --memory="$CONTAINER_MEM" \
        --memory-swap="$CONTAINER_MEM" \
        -v "$(pwd)":/app \
        -v "$TEMP_TEMPLATE_JOB":"$TEMP_TEMPLATE_JOB" \
        $GRYPE_IMG sbom:/app/sbom.json \
        -o template -t "$TEMP_TEMPLATE_JOB" > "$OUTPUT_FILE" 2>/dev/null

    # === Validate Report ===
    if [[ $? -eq 0 ]]; then
        local JOB_END=$SECONDS
        local DURATION=$((JOB_END - JOB_START))
        local FORMATED_TIME=$(format_time "$DURATION")
        echo "   ✅ [$SCAN_ID] Report generated: $PROJECT_NAME (Time: $FORMATED_TIME)"
        # Clean up location paths in HTML
        sed -i -E 's|<td>\[Location<RealPath="([^"]+)".*>\]</td>|<td>\1</td>|g' "$OUTPUT_FILE"
        echo "success" > "$RESULTS_DIR/$SCAN_ID"
        echo "$DURATION" > "$RESULTS_DIR/${SCAN_ID}_time"
    else
        echo "   ❌ [$SCAN_ID] Scan failed: $PROJECT_NAME"
        echo "fail" > "$RESULTS_DIR/$SCAN_ID"
    fi

    # 🗑️ Cleanup
    rm -f "$TEMP_TEMPLATE_JOB"
    cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
}

SCAN_ID=0
while IFS='|' read -r INDEX PROJECT_NAME DIR_NAME; do
    if [[ -z "$INDEX" ]]; then continue; fi
    
    ((SCAN_ID++))
    
    # Process project in background
    process_project "$INDEX" "$PROJECT_NAME" "$DIR_NAME" "$SCAN_ID" &
    
    ((ACTIVE_JOBS++))
    
    # Simple job control: wait if we hit MAX_PARALLEL
    if [ "$ACTIVE_JOBS" -ge "$MAX_PARALLEL" ]; then
        wait -n
        ((ACTIVE_JOBS--))
    fi
done < <(echo "$PROJECT_LIST")

# Wait for remaining jobs
wait

# === Final Summary ===
TOTAL_TIME=$((SECONDS - START_TIME))
FORMATED_TOTAL_TIME=$(format_time "$TOTAL_TIME")
SCAN_COUNT=$(ls "$RESULTS_DIR" | grep -v "_time" | wc -l)
SUCCESS_COUNT=$(grep -l "success" "$RESULTS_DIR"/* 2>/dev/null | wc -l)
FAIL_COUNT=$(grep -l "fail" "$RESULTS_DIR"/* 2>/dev/null | wc -l)

echo ""
echo "============================================================"
echo "📊 SCAN SUMMARY"
echo "============================================================"
echo "   Total projects processed: $SCAN_COUNT"
echo "   ✅ Successful scans: $SUCCESS_COUNT"
echo "   ❌ Failed scans: $FAIL_COUNT"
echo "   ⏱️  Total time taken: $FORMATED_TOTAL_TIME"
echo "   📁 Reports location: $REPORT_DIR"
echo "============================================================"
echo "✨ Bulk SCA Scan Complete!"
echo "============================================================"

# Exit with error if any scans failed
if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
