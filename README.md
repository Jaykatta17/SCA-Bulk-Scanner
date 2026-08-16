# SCA Scanner Tool

> Automated Software Composition Analysis (SCA) for bulk vulnerability detection using Syft and Grype

## 📋 Overview

This tool provides **automated bulk security scanning** of multiple software projects to identify vulnerabilities in dependencies. It reads project configurations from a CSV file, automatically clones repositories, and scans them using **Syft** (SBOM generation) and **Grype** (vulnerability scanning) from Anchore to create detailed HTML reports with CVE information.

## 🎯 Features

- **📊 Bulk Scanning**: Scan multiple projects in one run using CSV configuration
- **🔄 Automated Cloning**: Automatically clones repositories from Git URLs
- **📌 Commit Control**: Checkout specific commits or use latest
- **📦 SBOM Generation**: Creates Software Bill of Materials in CycloneDX JSON format
- **🔍 Vulnerability Detection**: Identifies known CVEs in project dependencies
- **🌿 Git Integration**: Automatically captures branch and commit information
- **📄 Custom HTML Reports**: Generates professional HTML reports with project metadata
- **🧹 Auto Cleanup**: Removes cloned repositories after scanning

## 📁 Project Structure

```
SCA/
├── scan_project.sh       # Main bulk scanner script
├── clone_repos.py        # Python script for CSV-based cloning
├── data/
│   └── target_projects.csv  # Project configuration file
├── template/
│   ├── html.tmpl         # HTML report template
│   └── report_template.xlsx
└── reports/              # Generated scan reports
```

## 🔧 Prerequisites

- **Docker**: Required to run Syft and Grype containers
- **Python 3**: For automated repository cloning
- **Git**: For cloning repositories
- **Bash**: Shell environment (WSL on Windows, native on Linux/Mac)

### Docker Images Used

- `anchore/syft:latest` - SBOM generation
- `anchore/grype:latest` - Vulnerability scanning

## 🚀 Usage

### Bulk Scan from CSV (Recommended)

This is the **primary workflow** for scanning multiple projects:

**Step 1:** Configure your projects in `data/target_projects.csv`:

```csv
project_name,git_url,branch,commit_id,maintainer,assessment_type
MyApp,https://github.com/org/myapp.git,main,,john,Initial
LegacyApp,https://github.com/org/legacy.git,develop,a1b2c3d,jane,Follow-up
```

**Step 2:** Run the bulk scanner:

```bash
./scan_project.sh
```

That's it! The script will:
1. 🔍 Iterate through all projects in the CSV
2. 📥 Clone each project one-by-one
3. 🔎 Scan the project for vulnerabilities
4. 📄 Generate the HTML report
5. 🧹 **Delete the project folder immediately** before moving to the next one

> [!TIP]
> This sequential processing ensures that you don't run out of disk space even when scanning dozens of large repositories simultaneously.

### CSV Configuration Format

The `data/target_projects.csv` file should contain the following columns:

| Column | Description | Required | Example |
|--------|-------------|----------|---------|
| `project_name` | Project identifier | ✅ Yes | `Gitleaks` |
| `git_url` | Git repository URL | ✅ Yes | `https://github.com/gitleaks/gitleaks.git` |
| `branch` | Git branch to scan | ✅ Yes | `main` or `develop` |
| `commit_id` | Specific commit hash (optional) | ❌ No | `a1b2c3d` or leave empty for latest |
| `maintainer` | Project maintainer | ❌ No | `john` |
| `assessment_type` | Assessment category | ❌ No | `Initial` or `Follow-up` |

**Example CSV:**

```csv
project_name,git_url,branch,commit_id,maintainer,assessment_type
Gitleaks,https://github.com/gitleaks/gitleaks.git,master,,jay,Initial
Trivy,https://github.com/aquasecurity/trivy.git,main,abc123,jane,Follow-up
Grype,https://github.com/anchore/grype.git,main,,bob,Initial
```

**Commit Handling:**
- **Empty `commit_id`**: Clones the latest commit from the specified branch
- **Specific `commit_id`**: Clones the branch and checks out that specific commit

## 📊 Report Output

Reports are saved in the `reports/` directory with the following naming convention:

```
SCA-{PROJECT_NAME}-{BRANCH}_{COMMIT}-{DATE}.html
```

**Example:**
```
SCA-myapp-main_a1b2c3d-2026-01-19.html
```

Each report includes:
- Project name
- Git branch
- Commit hash
- Complete list of vulnerabilities with:
  - CVE identifiers
  - Severity levels
  - Affected packages
  - Recommended fixes

## 🔍 How It Works

### Workflow

1. **SBOM Generation**
   - Syft scans the project directory
   - Identifies all software components and dependencies
   - Outputs CycloneDX JSON format SBOM

2. **Template Customization**
   - Injects project metadata (name, branch, commit) into HTML template
   - Creates temporary customized template

3. **Vulnerability Scanning**
   - Grype analyzes the SBOM
   - Matches components against CVE databases
   - Identifies known vulnerabilities

4. **Report Generation**
   - Generates HTML report using custom template
   - Includes all vulnerability details
   - Saves to reports directory

### Technical Details

Both scripts use Docker containers to ensure:
- Consistent scanning environment
- No local installation requirements
- Reproducible results across different systems

## ⚙️ Configuration

### Base Directory Configuration

Edit the `BASE_DIR` variable in both scripts to match your setup:

```bash
BASE_DIR="/path/to/SCA"  # Replace with your actual SCA storage directory
```

### Template Customization

Modify `template/html.tmpl` to customize report appearance. The template supports these placeholders:

- `PROJECT_VAR` - Replaced with project name
- `BRANCH_VAR` - Replaced with Git branch
- `COMMIT_VAR` - Replaced with Git commit hash

### Proxy Configuration (Optional)

If you're behind a corporate proxy, you can configure proxy settings in the Docker run commands within `scan_project.sh`:

```bash
docker run --rm -e GRYPE_BY_CVE=true --name grype-scanner \
     -e HTTPS_PROXY="http://user:pass@proxy.example.com:8080" \
     -e HTTP_PROXY="http://user:pass@proxy.example.com:8080" \
     -e NO_PROXY="internal.domain" \
     --network host \
     ...
```

## 📝 Example Output

```
============================================================
🚀 SCA Scanner - Bulk Scan Automation
============================================================
📁 Base directory: /path/to/SCA
📊 Reports will be saved in: /path/to/SCA/reports
📄 Using template: /path/to/SCA/template/html.tmpl
============================================================

📥 STEP 1: Cloning repositories from CSV configuration
============================================================
🚀 Repository Cloner - SCA Scanner
============================================================
📁 Clone directory: /path/to/SCA/cloned_projects
📄 Reading projects from: /path/to/SCA/data/target_projects.csv
============================================================

🔹 Cloning: Gitleaks
   URL: https://github.com/gitleaks/gitleaks.git
   Branch: master
   Commit: a1b2c3d (latest)
✅ Successfully cloned: Gitleaks

============================================================
📊 Cloning Summary:
   ✅ Successful: 1
   ❌ Failed: 0
============================================================

✨ All repositories cloned successfully!
🔜 Proceeding with SCA scanning...

🔍 STEP 2: Scanning cloned projects
============================================================

🔍 [1] Processing project: Gitleaks
   ⭐ Branch: master | Commit: a1b2c3d
   📦 Generating SBOM...
   🔎 Running Grype vulnerability scan...
   ✅ Report generated: SCA-Gitleaks-master_a1b2c3d-2026-01-19.html
   ------------------------------------------------------------

🗑️  STEP 3: Cleanup
============================================================
Removing cloned projects directory...
✅ Cloned projects directory removed.

============================================================
📊 SCAN SUMMARY
============================================================
   Total projects processed: 1
   ✅ Successful scans: 1
   ❌ Failed scans: 0
   📁 Reports location: /path/to/SCA/reports
============================================================
✨ Bulk SCA Scan Complete!
============================================================
```

## 🔄 Workflow Diagram

```
CSV File → Clone Repos → Generate SBOM → Scan Vulnerabilities → HTML Reports → Cleanup
   ↓            ↓             ↓                  ↓                    ↓            ↓
Configure   Python       Syft Docker        Grype Docker      Formatted     Removed
Projects    Script       Container          Container         Reports       Clones
```

## 🛡️ Security Considerations

- Reports may contain sensitive information about vulnerabilities
- Store reports securely
- Review and remediate identified vulnerabilities promptly
- Integrate scanning into CI/CD pipelines for continuous monitoring

## 🔄 CI/CD Integration

You can integrate these scripts into your CI/CD pipeline:

```yaml
# Example GitLab CI
sca-scan:
  stage: security
  script:
    - ./scan_project.sh $CI_PROJECT_DIR
  artifacts:
    paths:
      - reports/*.html
    expire_in: 30 days
```

## 🐛 Troubleshooting

### Docker Permission Issues

If you encounter permission errors:
```bash
sudo usermod -aG docker $USER
# Log out and back in
```

### Project Not Recognized as Git Repository

Ensure the project directory is initialized as a Git repository:
```bash
cd /path/to/project
git rev-parse --is-inside-work-tree
```

### Template Not Found

Verify the template path exists:
```bash
ls -la /path/to/SCA/template/html.tmpl
```

## 📚 Resources

- [Syft Documentation](https://github.com/anchore/syft)
- [Grype Documentation](https://github.com/anchore/grype)
- [CycloneDX Specification](https://cyclonedx.org/)
- [NIST NVD](https://nvd.nist.gov/) - National Vulnerability Database

## 📄 License

This project is licensed under the [Apache License 2.0](LICENSE).

### Third-Party Licenses & Attribution

This toolkit doesn't bundle any third-party source code, but it depends on
and integrates with the following tools and libraries at runtime. All
trademarks and copyrights belong to their respective owners.

| Component | Publisher | License | Usage |
|-----------|-----------|---------|-------|
| [Syft](https://github.com/anchore/syft) | Anchore, Inc. | Apache-2.0 | SBOM generation (`anchore/syft` Docker image) |
| [Grype](https://github.com/anchore/grype) | Anchore, Inc. | Apache-2.0 | Vulnerability scanning (`anchore/grype` Docker image) |
| [DataTables](https://datatables.net/) | SpryMedia Ltd | MIT | HTML report table (loaded via CDN) |
| [jQuery](https://jquery.com/) | OpenJS Foundation | MIT | HTML report scripting (loaded via CDN) |
| [pdfmake](https://github.com/bpampuch/pdfmake) | bpampuch | MIT | HTML report PDF export (loaded via CDN) |
| [JSZip](https://stuk.github.io/jszip/) | Stuart Knightley | MIT / GPLv3 (dual) | HTML report Excel export (loaded via CDN) |
| [Font Awesome Free](https://fontawesome.com/) | Fonticons, Inc. | Font Awesome Free License (icons: CC BY 4.0, code: MIT) | HTML report icons (loaded via CDN) |
| [Devicon](https://devicon.dev/) | Devicon contributors | MIT | HTML report package-type icons (loaded via CDN) |
| [Iconify](https://iconify.design/) | Iconify | MIT | HTML report fallback icons (loaded via CDN) |

Syft and Grype are not redistributed with this repository — they are pulled
as official Docker images at scan time and remain governed by Anchore's own
licensing. See each project's repository for full license text.

## 🤝 Support

For issues or questions, contact the security team.

---

**Last Updated**: January 2026
