#!/usr/bin/env python3
import requests
import argparse
from datetime import datetime, timedelta
import logging
import sys
import json
from pathlib import Path
from tabulate import tabulate
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import time
from html import escape
import utils.twitter as twitter

@dataclass
class ArtifactInfo:
    repo_name: str           # Repository name
    digest: str              # Unique artifact identifier
    last_pull_time: datetime # When last pulled (or None)
    size_bytes: int          # Storage size
    tags: List[str]          # Associated tags
    is_latest: bool          # Has 'latest' tag

class HarborCleanup:
    def __init__(self, config_file=None, **kwargs):
        if config_file:
            config = self._load_config(config_file)
            self.harbor_url = config['url']
            self.auth = (config['username'], config['password'])
            self.project_name = config['project']
            self.days_to_keep = config['days_to_keep']
            self.protected_tags = config['protected_tags']
            self.dry_run = config['dry_run']
            self.exclusions = config['exclusions']
            self.max_workers = max(1, config.get('max_workers', 10))  # Ensure at least 1

            log_config = config['logging']
            log_handlers = [logging.StreamHandler(sys.stdout)]
            if log_config.get('file'):
                log_handlers.append(logging.FileHandler(log_config['file']))
            logging.basicConfig(
                format=log_config['format'],
                level=getattr(logging, log_config['level']),
                handlers=log_handlers
            )
        else:
            self.harbor_url = kwargs['harbor_url']
            self.auth = (kwargs['username'], kwargs['password'])
            self.project_name = kwargs.get('project_name', '')
            self.days_to_keep = kwargs.get('days_to_keep', 2)
            self.protected_tags = kwargs.get('protected_tags', ['latest', 'stable', 'prod'])
            self.dry_run = kwargs.get('dry_run', False)
            self.exclusions = kwargs.get('exclusions', {})
            self.max_workers = max(1, kwargs.get('max_workers', 10))  # Ensure at least 1
            self.setup_logging()

        self.harbor_url = self.harbor_url.rstrip('/')
        self.cutoff_date = datetime.now() - timedelta(days=self.days_to_keep)
        self.logger = logging.getLogger('harbor-cleanup')
        
        # Thread-safe counters
        self._lock = threading.Lock()
        self._stats = {
            'repositories_processed': 0,
            'artifacts_checked': 0,
            'artifacts_deleted': 0,
            'artifacts_to_delete': 0,
            'errors': 0
        }

    def generate_html_report(self, artifacts_to_delete: List[ArtifactInfo], report_file='reports/cleanup_report.html', stats=None, total_size_bytes=0):
        html_content = [
            "<html>",
            "<head>",
            "<title>Harbor Cleanup Report</title>",
            '<link rel="stylesheet" href="https://cdn.datatables.net/1.13.5/css/jquery.dataTables.min.css" />',
            '<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>',
            '<script src="https://cdn.datatables.net/1.13.5/js/jquery.dataTables.min.js"></script>',
            "<style>",
            "body { font-family: Arial; margin: 20px; background: #f9f9f9; color: #333; }",
            "table { border-collapse: collapse; width: 100%; }",
            "th, td { border: 1px solid #ddd; padding: 8px; }",
            "th { background: #2980b9; color: white; }",
            "tr:nth-child(even) { background: #f2f2f2; }",
            "tr:hover { background: #d6eaf8; }",
            "tfoot tr { background-color: #ecf0f1; font-weight: bold; }",
            ".stats-block { background: #d9eefa; padding: 15px; margin-bottom: 20px; border-radius: 8px; color: #1b4f72; }",
            ".stats-block h2 { margin-top: 0; }",
            ".stats-block p { margin: 5px 0; font-weight: bold; }",
            ".dataTables_wrapper .dataTables_filter input { border: 1px solid #2980b9; padding: 4px; border-radius: 4px; }",
            ".dataTables_wrapper .dataTables_length select { border: 1px solid #2980b9; border-radius: 4px; padding: 3px; }",
            "table.dataTable thead th { background-color: #2980b9 !important; color: white !important; }",
            "table.dataTable thead .sorting:after, table.dataTable thead .sorting_asc:after, table.dataTable thead .sorting_desc:after { color: white !important; }",
            "</style>",
            "<script>",
            "$(document).ready(function() {",
            "  $('#cleanup-report').DataTable({",
            "    paging: true,",
            "    searching: true,",
            "    info: true,",
            "    pageLength: 50",
            "  });",
            "});",
            "</script>",
            "</head>",
            "<body>",
            f"<h1>Harbor Cleanup Report - {'Dry Run' if self.dry_run else 'Actual Run'}</h1>",
            "<div class='stats-block'>",
            "<h2>Summary Statistics</h2>",
            f"<p>Repositories Processed: {stats.get('repositories_processed', 0) if stats else 0}</p>",
            f"<p>Artifacts Checked: {stats.get('artifacts_checked', 0) if stats else 0}</p>",
            f"<p>Artifacts To Delete: {stats.get('artifacts_to_delete', 0) if stats else 0}</p>",
            f"<p>Artifacts Deleted: {stats.get('artifacts_deleted', 0) if stats else 0}</p>",
            f"<p>Errors Encountered: {stats.get('errors', 0) if stats else 0}</p>",
            f"<p>Total Size of Artifacts Processed: {total_size_bytes / (1024 * 1024 * 1024):.2f} GB</p>",
            "</div>",
            "<table id='cleanup-report'>",
            "<thead>",
            "<tr><th>Repository</th><th>Digest</th><th>Last Pull Time</th><th>Size (MB)</th><th>Is Latest</th></tr>",
            "</thead>",
            "<tbody>",
        ]

        total_size = 0
        for artifact in artifacts_to_delete:
            repo = escape(artifact.repo_name)
            digest = escape(artifact.digest[:12])
            pull_time = artifact.last_pull_time.strftime('%Y-%m-%d %H:%M') if artifact.last_pull_time and artifact.last_pull_time.year > 1 else 'Never pulled'
            size_mb = artifact.size_bytes / (1024 * 1024)
            total_size += artifact.size_bytes
            is_latest = "✅" if artifact.is_latest else ""
            html_content.append(f"<tr><td>{repo}</td><td>{digest}</td><td>{pull_time}</td><td>{size_mb:.2f}</td><td>{is_latest}</td></tr>")

        html_content.append("</tbody>")

        # Total space cleared footer row
        total_size_GB = total_size / (1024 * 1024 * 1024)
        html_content.append(
            f"<tfoot><tr><td colspan='3'><strong>Total space cleared</strong></td>"
            f"<td colspan='2'><strong>{total_size_GB:.2f} GB</strong></td></tr></tfoot>"
        )

        html_content.append("</table>")
        html_content.append("</body>")
        html_content.append("</html>")

        with open(report_file, "w") as f:
            f.write("\n".join(html_content))

        self.logger.info(f"HTML report saved to {report_file}")




    def _load_config(self, config_file):
        config_path = Path(config_file)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")

        try:
            with open(config_path) as f:
                config = json.load(f)

            if 'harbor' not in config:
                raise ValueError("Missing 'harbor' section in config")

            harbor_config = config['harbor']
            required = ['url', 'username', 'password']
            if not all(k in harbor_config for k in required):
                raise ValueError(f"Config missing required fields: {required}")

            return {
                'url': harbor_config['url'],
                'username': harbor_config['username'],
                'password': harbor_config['password'],
                'project': harbor_config.get('project', ''),
                'days_to_keep': harbor_config.get('days_to_keep', 2),
                'dry_run': harbor_config.get('dry_run', False),
                'protected_tags': harbor_config.get('protected_tags', ['latest', 'stable', 'prod']),
                'max_workers': harbor_config.get('max_workers', 10),
                'logging': harbor_config.get('logging', {
                    'level': 'INFO',
                    'format': '%(asctime)s - %(levelname)s - %(message)s',
                    'file': None
                }),
                'exclusions': harbor_config.get('exclusions', {
                    'repositories': [],
                    'tags_patterns': []
                }),
                'notifications': harbor_config.get('notifications', {
                    'email': {'enabled': False},
                    'slack': {'enabled': False}
                })
            }
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON config: {str(e)}")

    def setup_logging(self):
        logging.basicConfig(
            format='%(asctime)s - %(levelname)s - %(message)s',
            level=logging.INFO,
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger('harbor-cleanup')

    def make_api_request(self, method, endpoint, **kwargs):
        url = f"{self.harbor_url}{endpoint}"
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                response = requests.request(
                    method,
                    url,
                    auth=self.auth,
                    verify=Path(__file__).with_name('cert.pem'),
                    timeout=30,  # Add timeout
                    **kwargs
                )
                response.raise_for_status()
                return response
            except requests.exceptions.SSLError:
                self.logger.error("SSL error: certificate verify failed. You may need to trust the Harbor cert.")
                raise
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"API request failed (attempt {attempt + 1}/{max_retries}): {method} {url} - {str(e)}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    self.logger.error(f"API request failed after {max_retries} attempts: {method} {url} - {str(e)}")
                    raise

    def get_repositories(self):
        page = 1
        page_size = 100
        all_repos = []

        while True:
            endpoint = f"/api/v2.0/projects/{self.project_name}/repositories?page={page}&page_size={page_size}"
            response = self.make_api_request('GET', endpoint)
            repos = response.json()
            if not repos:
                break
            all_repos.extend(repos)
            if len(repos) < page_size:
                break
            page += 1

        return all_repos

    def get_artifacts(self, repository_name):
        page = 1
        page_size = 100
        all_artifacts = []

        while True:
            endpoint = f"/api/v2.0/projects/{self.project_name}/repositories/{repository_name}/artifacts?page={page}&page_size={page_size}"
            response = self.make_api_request('GET', endpoint)
            artifacts = response.json()
            if not artifacts:
                break
            all_artifacts.extend(artifacts)
            if len(artifacts) < page_size:
                break
            page += 1

        return all_artifacts

    def get_artifact_details(self, repository_name, artifact_digest):
        endpoint = f"/api/v2.0/projects/{self.project_name}/repositories/{repository_name}/artifacts/{artifact_digest}"
        try:
            response = self.make_api_request('GET', endpoint)
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to get artifact details for {repository_name}@{artifact_digest[:12]}: {str(e)}")
            return None

    def delete_artifact(self, repository_name, artifact_digest):
        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would delete {self.project_name}/{repository_name}@{artifact_digest[:12]}")
            return True
        endpoint = f"/api/v2.0/projects/{self.project_name}/repositories/{repository_name}/artifacts/{artifact_digest}"
        try:
            self.make_api_request('DELETE', endpoint)
            self.logger.info(f"Deleted {self.project_name}/{repository_name}@{artifact_digest[:12]}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete {self.project_name}/{repository_name}@{artifact_digest[:12]}: {str(e)}")
            return False

    def process_artifact(self, repo_name: str, artifact: Dict[str, Any]) -> Optional[ArtifactInfo]:
        """Process a single artifact and return ArtifactInfo if it should be deleted"""
        try:
            artifact_digest = artifact['digest']
            details = self.get_artifact_details(repo_name, artifact_digest)
            
            # Safety check for None details
            if not details:
                self.logger.warning(f"No details returned for artifact {repo_name}@{artifact_digest[:12]}")
                return None
            
            with self._lock:
                self._stats['artifacts_checked'] += 1

            if self.should_skip_artifact(repo_name, details):
                return None

            if not self.is_artifact_old(details):
                return None

            # Artifact should be deleted
            pull_time = self.get_last_pull_time(details)
            tags = details.get('tags') or []  # Handle None tags
            tags = [tag.get('name', '') for tag in tags if tag and isinstance(tag, dict)]
            size_bytes = details.get('size', 0) or 0
            is_latest = 'latest' in tags

            return ArtifactInfo(
                repo_name=repo_name,
                digest=artifact_digest,
                last_pull_time=pull_time,
                size_bytes=size_bytes,
                tags=tags,
                is_latest=is_latest
            )

        except Exception as e:
            with self._lock:
                self._stats['errors'] += 1
            self.logger.error(f"Error processing artifact {repo_name}@{artifact.get('digest', 'unknown')[:12]}: {str(e)}")
            return None

    def process_repository(self, repo: Dict[str, Any]) -> List[ArtifactInfo]:
        """Process all artifacts in a repository and return list of artifacts to delete"""
        repo_name = repo['name'].split('/')[-1]
        
        # Filter repositories
        if not ('cdp' in repo_name or 'sdp' in repo_name):
            self.logger.info(f"Skipping repository {repo_name} as it does not include 'cdp' or 'sdp'")
            return []

        self.logger.info(f"Processing repository: {repo_name}")
        artifacts_to_delete = []
        
        try:
            artifacts = self.get_artifacts(repo_name)
            self.logger.info(f"  Found {len(artifacts)} artifacts in {repo_name}")

            # Process artifacts in parallel within the repository
            # Ensure we have at least 1 worker, but don't exceed artifact count or reasonable limit
            artifact_workers = max(1, min(5, len(artifacts)))
            with ThreadPoolExecutor(max_workers=artifact_workers) as executor:
                future_to_artifact = {
                    executor.submit(self.process_artifact, repo_name, artifact): artifact
                    for artifact in artifacts
                }
                
                for future in as_completed(future_to_artifact):
                    result = future.result()
                    if result:
                        artifacts_to_delete.append(result)

            with self._lock:
                self._stats['repositories_processed'] += 1
                self._stats['artifacts_to_delete'] += len(artifacts_to_delete)
                
            self.logger.info(f"  Repository {repo_name}: {len(artifacts_to_delete)} artifacts marked for deletion")

        except Exception as e:
            with self._lock:
                self._stats['errors'] += 1
            self.logger.error(f"Error processing repository {repo_name}: {str(e)}")

        return artifacts_to_delete

    def delete_artifacts_batch(self, artifacts_batch: List[ArtifactInfo]) -> int:
        """Delete a batch of artifacts and return count of successful deletions"""
        deleted_count = 0
        for artifact in artifacts_batch:
            if self.delete_artifact(artifact.repo_name, artifact.digest):
                deleted_count += 1
        return deleted_count

    def cleanup(self):
        start_time = time.time()
        self.logger.info(f"Starting cleanup for project '{self.project_name}' (keeping images pulled in last {self.days_to_keep} days)")
        self.logger.info(f"Using {self.max_workers} worker threads")
        
        repositories = self.get_repositories()
        # Filter repositories early
        filtered_repos = [repo for repo in repositories 
                         if 'cdp' in repo['name'].split('/')[-1] or 'sdp' in repo['name'].split('/')[-1]]
        
        self.logger.info(f"Found {len(repositories)} repositories, {len(filtered_repos)} match filter criteria")

        all_artifacts_to_delete = []

        # Process repositories in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_repo = {
                executor.submit(self.process_repository, repo): repo
                for repo in filtered_repos
            }
            
            for future in as_completed(future_to_repo):
                repo = future_to_repo[future]
                try:
                    artifacts_to_delete = future.result()
                    all_artifacts_to_delete.extend(artifacts_to_delete)
                except Exception as e:
                    repo_name = repo['name'].split('/')[-1]
                    self.logger.error(f"Repository processing failed for {repo_name}: {str(e)}")

        # Handle results
        if self.dry_run:
            self._display_dry_run_results(all_artifacts_to_delete)
        else:
            self._delete_artifacts_parallel(all_artifacts_to_delete)

        # Display final statistics
        elapsed_time = time.time() - start_time
        total_size_mb = sum(artifact.size_bytes for artifact in all_artifacts_to_delete) / (1024 * 1024)
        
        self.logger.info(f"Cleanup completed in {elapsed_time:.2f} seconds")
        self.logger.info(f"Statistics: {self._stats}")
        self.logger.info(f"Total size of artifacts processed: {total_size_mb:.2f} MB ({total_size_mb/1024:.2f} GB)")
        
        if not self.dry_run and all_artifacts_to_delete:
            deleted_size_mb = sum(artifact.size_bytes for artifact in all_artifacts_to_delete 
                                if self._stats['artifacts_deleted'] > 0) * (self._stats['artifacts_deleted'] / len(all_artifacts_to_delete)) / (1024 * 1024)
            self.logger.info(f"Estimated size freed: {deleted_size_mb:.2f} MB ({deleted_size_mb/1024:.2f} GB)")

        return all_artifacts_to_delete

    def _display_dry_run_results(self, artifacts_to_delete: List[ArtifactInfo]):
        """Display dry run results in a table"""
        if artifacts_to_delete:
            # Remove duplicates based on repo_name + digest combination
            unique_artifacts = {}
            for artifact in artifacts_to_delete:
                key = f"{artifact.repo_name}:{artifact.digest}"
                unique_artifacts[key] = artifact
            
            unique_list = list(unique_artifacts.values())
            
            self.logger.info(f"Dry-run complete: {len(unique_list)} unique artifacts would be deleted")
            print(f"\nDry-run results: {len(unique_list)} unique artifacts to delete:\n")
            
            table_data = []
            total_size = 0
            for artifact in unique_list:
                # Better date formatting
                if artifact.last_pull_time:
                    # Check if it's the epoch date (1-01-01) which indicates no real pull time
                    if artifact.last_pull_time.year == 1:
                        pull_time_str = 'Never pulled'
                    else:
                        pull_time_str = artifact.last_pull_time.strftime('%Y-%m-%d %H:%M')
                else:
                    pull_time_str = 'N/A'
                    
                size_mb = artifact.size_bytes / (1024 * 1024)
                total_size += size_mb
                
                table_data.append([
                    artifact.repo_name,
                    artifact.digest[:12],
                    pull_time_str,
                    f"{size_mb:.2f} MB",
                    '✅' if artifact.is_latest else ''
                ])
            
            headers = ['Repository', 'Digest', 'Last Pull Time', 'Size', 'Is Latest']
            print(tabulate(table_data, headers=headers, tablefmt='github'))
            print(f"\nTotal size that would be freed: {total_size:.2f} MB ({total_size/1024:.2f} GB)")
        else:
            print("\nDry-run complete: No artifacts to delete.\n")

    def _delete_artifacts_parallel(self, artifacts_to_delete: List[ArtifactInfo]):
        """Delete artifacts in parallel batches"""
        if not artifacts_to_delete:
            self.logger.info("No artifacts to delete")
            return

        self.logger.info(f"Deleting {len(artifacts_to_delete)} artifacts in parallel...")
        
        # Group artifacts into batches for parallel deletion
        batch_size = max(1, len(artifacts_to_delete) // self.max_workers)
        batches = [artifacts_to_delete[i:i + batch_size] 
                  for i in range(0, len(artifacts_to_delete), batch_size)]
        
        total_deleted = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_batch = {
                executor.submit(self.delete_artifacts_batch, batch): batch
                for batch in batches
            }
            
            for future in as_completed(future_to_batch):
                try:
                    deleted_count = future.result()
                    total_deleted += deleted_count
                except Exception as e:
                    self.logger.error(f"Batch deletion failed: {str(e)}")

        with self._lock:
            self._stats['artifacts_deleted'] = total_deleted
            
        self.logger.info(f"Successfully deleted {total_deleted}/{len(artifacts_to_delete)} artifacts")

    def should_skip_artifact(self, repo_name, artifact_details):
        # Safety check for None artifact_details
        if not artifact_details:
            self.logger.warning(f"No artifact details for {repo_name}, skipping")
            return True
            
        tags = artifact_details.get('tags') or []
        
        # Ensure tags is iterable and contains valid tag objects
        if not isinstance(tags, list):
            self.logger.warning(f"Invalid tags format for {repo_name}, skipping")
            return True

        # Check protected tags
        for tag in tags:
            if tag and isinstance(tag, dict):
                tag_name = tag.get('name', '')
                if tag_name in self.protected_tags:
                    return True

        # Check repository exclusions
        if repo_name in self.exclusions.get('repositories', []):
            return True

        # Check tag pattern exclusions
        for tag in tags:
            if tag and isinstance(tag, dict):
                tag_name = tag.get('name', '')
                for pattern in self.exclusions.get('tags_patterns', []):
                    if pattern.startswith('*') and tag_name.endswith(pattern[1:]):
                        return True
                    if pattern.endswith('*') and tag_name.startswith(pattern[:-1]):
                        return True

        return False

    def is_artifact_old(self, artifact_details):
        pull_time = self.get_last_pull_time(artifact_details)
        if not pull_time:
            self.logger.debug("  Artifact has no pull history - marking as old")
            return True
        is_old = pull_time < self.cutoff_date
        if is_old:
            self.logger.debug(f"  Artifact last pulled on {pull_time} (older than cutoff {self.cutoff_date})")
        return is_old

    def get_last_pull_time(self, artifact_details):
        # Try to get pull time first
        if 'pull_time' in artifact_details and artifact_details['pull_time']:
            try:
                pull_time = datetime.strptime(artifact_details['pull_time'], '%Y-%m-%dT%H:%M:%S.%fZ')
                # Check if it's a meaningful date (not epoch)
                if pull_time.year > 1970:
                    return pull_time
            except ValueError:
                pass
        
        # Try alternative timestamp formats
        if 'push_time' in artifact_details and artifact_details['push_time']:
            try:
                push_time = datetime.strptime(artifact_details['push_time'], '%Y-%m-%dT%H:%M:%S.%fZ')
                if push_time.year > 1970:
                    return push_time
            except ValueError:
                pass
                
        # Try created time as last resort
        if 'extra_attrs' in artifact_details and 'created' in artifact_details['extra_attrs']:
            try:
                created_time = datetime.strptime(artifact_details['extra_attrs']['created'], '%Y-%m-%dT%H:%M:%S.%fZ')
                if created_time.year > 1970:
                    return created_time
            except ValueError:
                pass
        
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Harbor Docker Image Cleanup (Parallelized)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    default_config_path = Path(__file__).parent / "config.json"
    parser.add_argument('--config', type=str, default=str(default_config_path), help='Path to JSON configuration file')
    parser.add_argument('--days-to-keep', type=int, help='Override config: Number of days to keep unpulled images')
    parser.add_argument('--dry-run', action='store_true', help='Override config: Enable dry-run mode')
    parser.add_argument('--debug', action='store_true', help='Override config: Enable debug logging')
    parser.add_argument('--max-workers', type=int, help='Override config: Maximum number of worker threads')

    args = parser.parse_args()

    try:
        cleaner = HarborCleanup(config_file=args.config)

        if args.days_to_keep is not None:
            cleaner.days_to_keep = args.days_to_keep
            cleaner.cutoff_date = datetime.now() - timedelta(days=cleaner.days_to_keep)

        if args.dry_run:
            cleaner.dry_run = True

        if args.max_workers is not None:
            cleaner.max_workers = max(1, args.max_workers)  # Ensure at least 1

        if args.debug:
            cleaner.logger.setLevel(logging.DEBUG)
            cleaner.logger.debug("Debug logging enabled")

        artifacts_to_delete = cleaner.cleanup()

        total_size_bytes = sum(artifact.size_bytes for artifact in artifacts_to_delete)


        # Add this line to generate the report
        cleaner.generate_html_report(artifacts_to_delete, stats=cleaner._stats, total_size_bytes=total_size_bytes)

        twitter.main()
        
        sys.exit(0)

    except Exception as e:
        logging.error(f"Cleanup failed: {str(e)}", exc_info=args.debug)
        sys.exit(1)
