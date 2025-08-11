# Harbor Docker Image Cleanup Utility

## Overview
Automatically clean up old Docker images from Harbor registry by deleting artifacts that haven't been pulled recently, while preserving protected tags and following exclusion rules.

## Overview  
Automatically clean up old Docker images from Harbor registry by deleting artifacts that haven't been pulled recently, while preserving protected tags and following exclusion rules.  

## Table of Contents  
1. [Overview](#overview)  
2. [Features](#features)  
3. [Enhanced Features](#enhanced-features)  
4. [Files Included](#files-included)  
5. [Installation](#installation)  
6. [Configuration Options](#configuration-options)  
7. [Certificates](#-certificates)  
8. [Logic Flow](#logic-flow)  
9. [Expected Performance Gains](#expected-performance-gains)  
10. [Example Output (Dry Run)](#-example-output-dry-run)  
11. [Notes](#-notes)  
12. [requirements.txt](#requirementstxt)  
13. [Command Line Usage](#command-line-usage)  
14. [Customization](#customization)  
15. [Troubleshooting](#troubleshooting)  
16. [Summary](#summary)

## Features  
- **Targeted Cleanup** – Processes only Harbor projects matching specific naming criteria 
- **Protected Tag Preservation** – Safeguards critical tags such as `latest`, `stable`, `prod`, and user-defined ones.  
- **Custom Retention Period** – Configure the number of days to keep images before deletion.  
- **Exclusion Rules** – Skip specific repositories and tags matching wildcard patterns.  
- **Dry-Run Mode** – Preview cleanup actions without deleting any artifacts.  
- **Parallel Processing** – Uses concurrent workers to process multiple repositories and artifacts simultaneously, greatly reducing cleanup time.  
- **Detailed Logging** – Supports configurable log formats, log levels, and optional log file output for auditability in CI/CD pipelines.  
- **Config File Support** – Reads settings from a JSON configuration file with validation.  
- **Robust Error Handling** – Continues processing even if individual operations fail, with proper exit codes.  
- **Certificate Validation** – Supports TLS verification with custom `cert.pem`.  
- **Safe Defaults** – Defaults to dry-run mode if no explicit deletion request is made.  
- **Comprehensive Reporting** – Generates statistics on processed repositories, deleted artifacts, and reclaimed storage space.  

## Enhanced Features
- Supports JSON configuration for credentials and settings
- Maintains all original functionality
- Added config file validation
- Improved error handling for missing configurations

## Configuration Options
You must provide a configuration JSON file. It should be structured like this:

| Key Path                      | Type           | Description                                                    | Example / Notes                                    |
|------------------------------|----------------|----------------------------------------------------------------|---------------------------------------------------|
| `harbor.url`                  | String         | Harbor base URL                                               | `"https://harbor.example.com"`                     |
| `harbor.username`             | String         | Harbor username                                              | `"your_username"`                                  |
| `harbor.password`             | String         | Harbor password — **leave empty here; fetch from Vault**      | `""`                                               |
| `harbor.project`              | String         | Harbor project name                                           | `"gibbersih"`                           |
| `harbor.days_to_keep`         | Integer        | Number of days to keep images                                | `1`                                                |
| `harbor.dry_run`              | Boolean        | If true, no deletion will be performed                       | `true`                                             |
| `harbor.protected_tags`       | List[String]   | Tags that should never be deleted                            | `["latest", "stable", "prod", "release", "v1", "main"]` |
| `harbor.logging.level`        | String         | Log level                                                    | `"INFO"`                                           |
| `harbor.logging.format`       | String         | Log format                                                  | `"%(asctime)s - %(levelname)s - %(message)s"`     |
| `harbor.logging.file`         | String         | Log file path                                               | `"logs/harbor-cleanup.log"`                        |
| `harbor.exclusions.repositories` | List[String] | List of repo names excluded from cleanup                   | `["critical-service", "infra/base-images"]`       |
| `harbor.exclusions.tags_patterns` | List[String] | Patterns of tags excluded from cleanup                      | `["*-backup", "archive-*"]`                        |
| `smtp_config.host`            | String         | SMTP server host                                           | `"smtp.example.com"`                               |
| `smtp_config.port`            | Integer        | SMTP server port                                           | `587` (typical SMTP port)                          |
| `smtp_config.username`        | String         | SMTP username                                             | `"your_smtp_user@example.com"`                     |
| `smtp_config.password`        | String         | SMTP password — **leave empty here; fetch from Vault**        | `""`                                               |
| `smtp_config.use_tls`         | Boolean        | Whether to use TLS for SMTP connection                     | `true`                                             |
| `report_recipients`           | List[String]   | List of email addresses to send the report to              | `["recipient1@example.com"]`                        |
| `report_cc`                   | List[String]   | Optional list of email addresses to CC                      | `[]`                                               |


## 🔐 Certificates
The script expects a cert.pem file to validate Harbor's TLS certificate.

Place the cert.pem file in the same directory as the script (or adjust the code if needed).

## Logic Flow

### Repo Filtering 
```text
Repository name contains 'cdp' OR 'sdp'? 
    ↓ NO: Skip
    ↓ YES: Process
```

### Artifact evaluation
```text
Has protected tags? → SKIP
    ↓ NO
In exclusion list? → SKIP
    ↓ NO
Matches exclusion pattern? → SKIP
    ↓ NO
Has pull_time? → Use pull_time
    ↓ NO: Use creation_time
    ↓ NO TIME: Mark as old
    ↓
pull_time < cutoff_date? → DELETE
    ↓ NO: KEEP
```

## Expected Performance Gains
### Before (sequencial)

```text
Process Repo 1 → Process Repo 2 → Process Repo 3...
    ↓              ↓              ↓
All artifacts   All artifacts   All artifacts
sequentially    sequentially    sequentially
```

### After (Parallel)

```text
Process Repo 1, 2, 3... simultaneously
    ↓
Each repo processes artifacts concurrently
    ↓
Deletions happen in parallel batches
```

### Typical Improvements

- Small deployments (1-10 repos): 3-5x faster
- Medium deployments (10-50 repos): 5-10x faster
- Large deployments (50+ repos): 10-20x faster

## 🧪 Example Output (Dry Run)
# Example Email Output

**Subject:** Harbor Cleanup Report  
**From:** someone@somewhere.com  
**To:** recipient1@example.com, recipient2@example.com  
**Cc:** ccperson@example.com  

---

## Harbor Cleanup Report - Dry Run

### Summary Statistics
- **Repositories Processed:** 12  
- **Artifacts Checked:** 142  
- **Artifacts To Delete:** 25  
- **Artifacts Deleted:** 0  
- **Errors Encountered:** 0  
- **Total Size of Artifacts Processed:** 18.75 GB  

### Cleanup Details

| Repository       | Digest          | Last Pull Time       | Size (MB) | Is Latest |
|------------------|-----------------|---------------------|-----------|-----------|
| myapp/backend    | sha256:abc123xyz| 2025-08-01 12:34:56 | 150       | No        |
| infra/base-images | sha256:def456uvw| 2025-07-20 09:20:11 | 300       | Yes       |
| *... more rows ...* |                 |                     |           |           |

---

Regards,  
*Your Friendly Neighbourhood Hoover Bot*


## ❗ Notes
Only repositories that include "cdp" or "sdp" in their names are processed.

If an artifact has no pull history, it's assumed to be old and marked for deletion.

Deletion requires proper permissions in Harbor.

## requirements.txt
```text
requests>=2.25.1
python-dotenv>=0.19.0
tabulate==0.9.0
```

# Command Line Usage

```bash
# Basic usage with config file
python3 hoover.py --config config.json --days-to-keep 14 --max-workers 20

# Safe testing
python3 hoover.py --config config.json --dry-run --debug

# Twitter sends report via email
python3 -m utils.twitter
```

## Customization
### Modify Protected Tags
- Edit the should_skip_artifact() method:

```python
def should_skip_artifact(self, artifact_details):
    protected_tags = ['latest', 'prod', 'stable', 'release']  # Add your tags
    tags = artifact_details.get('tags', [])
    return any(tag.get('name', '') in protected_tags for tag in tags)
```

## Change Logging Format
### Edit setup_logging():

```python
def setup_logging(self):
    logging.basicConfig(
        format='[%(asctime)s] %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S'
    )
```

# Harbor Cleanup Enhancements

## Email Report Feature
Starting with this version, after each run of the Harbor Cleanup (hoover.py), an HTML report is generated and automatically emailed to a configurable list of recipients. This helps you stay informed about the cleanup activity without manually checking the logs.

## How It Works
- The cleanup report is saved as reports/cleanup_report.html.

- After cleanup, the script calls twitter.py which sends the report via email.

- Email recipients and SMTP server details are configured in config.json.

- The SMTP password is securely retrieved from Vault using the credential_extract.py helper.

- An optional CC list can also be specified to send copies of the report.


# Summary 
This script transforms a sequential cleanup process into a highly parallel, robust system that:

- Scales with the number of CPU cores and network capacity
- Protects critical images with multiple safety mechanisms
- Reports comprehensive statistics and progress
- Handles errors gracefully without stopping the entire process
- Provides safe testing through dry-run mode

The parallelization primarily targets I/O-bound operations (API calls), making it ideal for Harbor cleanup tasks where network latency is the main bottleneck.
