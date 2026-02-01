#!/bin/bash

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

# === Step 1: Scan projects sequentially from CSV ===
echo ""
echo "🔍 STEP 1: Processing projects sequentially"
echo "============================================================"

# Get project info from CSV using the list flag
# Output format: index|original_name|dir_name
PROJECT_LIST=$(python3 "$BASE_DIR/clone_repos.py" --list)

SCAN_COUNT=0
SUCCESS_COUNT=0
FAIL_COUNT=0

# Use a while loop with process substitution to ensure variables persist outside the loop
while IFS='|' read -r INDEX PROJECT_NAME DIR_NAME; do
    if [[ -z "$INDEX" ]]; then continue; fi
    
    ((SCAN_COUNT++))

    echo ""
    echo "🚀 [$SCAN_COUNT] Processing project: $PROJECT_NAME (Dir: $DIR_NAME)"
    
    # 📥 Clone the specific project by its index to avoid name collisions
    echo "   📥 Cloning repository..."
    python3 "$BASE_DIR/clone_repos.py" --index-id "$INDEX"
    
    if [ $? -ne 0 ]; then
        echo "   ❌ Repository cloning failed for $PROJECT_NAME. Skipping."
        ((FAIL_COUNT++))
        continue
    fi

    PROJECT_PATH="$BASE_DIR/cloned_projects/$DIR_NAME"
    
    # Enter project directory
    cd "$PROJECT_PATH" || {
        echo "   ❌ Failed to enter directory: $PROJECT_PATH"
        ((FAIL_COUNT++))
        continue
    }

    # === Check if it's a Git repo ===
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "   ⚠️  Skipping $PROJECT_NAME (not a Git repository)"
        ((FAIL_COUNT++))
        # Cleanup anyway
        cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
        continue
    fi

    # === Get current branch and commit ID ===
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    SAFE_BRANCH=${BRANCH//\//_}
    COMMIT=$(git rev-parse --short HEAD)

    echo "   ⭐ Branch: $BRANCH | Commit: $COMMIT"

    # === Generate SBOM ===
    echo "   📦 Generating SBOM..."
    docker run --rm -e SYFT_CHECK_FOR_APP_UPDATE=false \
        -v "$(pwd):/app" \
        $SYFT_IMG /app/ \
        -o cyclonedx-json > sbom.json

    if [ $? -ne 0 ]; then
        echo "   ❌ SBOM generation failed for $PROJECT_NAME"
        ((FAIL_COUNT++))
        # Cleanup
        cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
        continue
    fi

    # === Inject Variables into Template ===
    # Use | as delimiter in sed to avoid issues with / in variables
    sed \
        -e "s|PROJECT_VAR|$PROJECT_NAME|g" \
        -e "s|BRANCH_VAR|$BRANCH|g" \
        -e "s|COMMIT_VAR|$COMMIT|g" \
        "$TEMPLATE_PATH" > "$TEMP_TEMPLATE"

    # === Run Grype Scan ===
    echo "   🔎 Running Grype vulnerability scan..."
    # Sanitize project name for filename
    SAFE_PROJECT_NAME=$(echo "$PROJECT_NAME" | sed 's/[^a-zA-Z0-9\-_]/_/g')
    OUTPUT_FILE="$REPORT_DIR/SCA-${SAFE_PROJECT_NAME}-${SAFE_BRANCH}_${COMMIT}-${SCAN_DATE}.html"

    docker run --rm -e GRYPE_BY_CVE=true --name "grype-scanner-$SCAN_COUNT" \
         -v "$(pwd)":/app \
         -v "$TEMP_TEMPLATE":"$TEMP_TEMPLATE" \
          $GRYPE_IMG sbom:/app/sbom.json \
          -o template -t "$TEMP_TEMPLATE" > "$OUTPUT_FILE"

    # === Validate Report ===
    if [[ $? -eq 0 ]]; then
        echo "   ✅ Report generated: $(basename "$OUTPUT_FILE")"
        # Clean up location paths in HTML
        find "$REPORT_DIR" -type f -name "$(basename "$OUTPUT_FILE")" -exec sed -i -E 's|<td>\[Location<RealPath="([^"]+)".*>\]</td>|<td>\1</td>|g' {} +;
        ((SUCCESS_COUNT++))
    else
        echo "   ❌ Error generating report for $PROJECT_NAME"
        ((FAIL_COUNT++))
    fi

    # 🗑️ Cleanup cloned project immediately
    echo "   🗑️  Cleaning up project folder..."
    cd "$BASE_DIR" && rm -rf "$PROJECT_PATH"
    
    echo "   ------------------------------------------------------------"
done < <(echo "$PROJECT_LIST")

# === Final Summary ===

echo ""
echo "============================================================"
echo "📊 SCAN SUMMARY"
echo "============================================================"
echo "   Total projects processed: $SCAN_COUNT"
echo "   ✅ Successful scans: $SUCCESS_COUNT"
echo "   ❌ Failed scans: $FAIL_COUNT"
echo "   📁 Reports location: $REPORT_DIR"
echo "============================================================"
echo "✨ Bulk SCA Scan Complete!"
echo "============================================================"

# Exit with error if any scans failed
if [ $FAIL_COUNT -gt 0 ]; then
    exit 1
fi
