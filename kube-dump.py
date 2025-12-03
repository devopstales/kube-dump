#!/usr/bin/env python3
import os
import sys
import tarfile
import gzip
import bz2
import lzma
import shutil
import logging
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple, Dict, Any

import click
import yaml
import requests
from kubernetes import config, client
from kubernetes.client.rest import ApiException
from git import Repo
from git.exc import InvalidGitRepositoryError


# === Logging (match original style) ===
class KubeDumpFormatter(logging.Formatter):
    def __init__(self, with_timestamp: bool = False):
        super().__init__()
        self.with_timestamp = with_timestamp

    def format(self, record):
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            out = f"{record.levelname}: {msg}"
        else:
            out = msg
        if self.with_timestamp:
            ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            out = f"{ts} {out}"
        return out

def setup_logger(silent: bool = False, with_timestamp: bool = False):
    logger = logging.getLogger("kube-dump")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr if silent else sys.stdout)
    handler.setFormatter(KubeDumpFormatter(with_timestamp=with_timestamp))
    handler.setLevel(logging.WARNING if silent else logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger

logger = logging.getLogger("kube-dump")


# === Raw API call ===
def call_k8s_api(path: str) -> Dict[str, Any]:
    api_client = client.ApiClient()
    return api_client.call_api(
        path,
        "GET",
        auth_settings=["BearerToken"],
        response_type="object",
        _return_http_data_only=True,
    )


# === Clean resource (keep apiVersion/kind!) ===
def clean_resource(obj: Dict[str, Any], detailed: bool = False) -> Dict[str, Any]:
    if not detailed:
        obj.pop("status", None)
        meta = obj.get("metadata", {})
        for key in ["uid", "resourceVersion", "generation", "managedFields", "creationTimestamp"]:
            meta.pop(key, None)
        obj["metadata"] = meta
    return obj


# === Save object ===
def save_object(obj: Dict[str, Any], base_dir: Path, resource_name: str, namespace: Optional[str] = None):
    name = obj.get("metadata", {}).get("name")
    if not name:
        return
    if namespace:
        path = base_dir / "namespaces" / namespace / resource_name / f"{name}.yaml"
    else:
        path = base_dir / "cluster" / resource_name / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Reorder keys to have apiVersion and kind first (standard k8s manifest order)
    ordered = {}
    for key in ["apiVersion", "kind", "metadata", "spec", "data", "stringData", "type"]:
        if key in obj:
            ordered[key] = obj[key]
    # Add remaining keys
    for key, value in obj.items():
        if key not in ordered:
            ordered[key] = value
    
    with path.open("w") as f:
        yaml.dump(ordered, f, default_flow_style=False, sort_keys=False)


# === Discover all readable API resources ===
# Returns tuples of (group, version, res_name, namespaced, kind)
def discover_resources() -> Tuple[List[Tuple[str, str, str, bool, str]], List[Tuple[str, str, str, bool, str]]]:
    ns_list = []
    cluster_list = []

    # Core v1
    try:
        core = call_k8s_api("/api/v1")
        for res in core.get("resources", []):
            if "list" in res.get("verbs", []):
                item = ("", "v1", res["name"], res.get("namespaced", False), res.get("kind", ""))
                (ns_list if item[3] else cluster_list).append(item)
    except Exception as e:
        logger.debug(f"Failed to discover core v1: {e}")

    # Other API groups
    try:
        groups = call_k8s_api("/apis")
        for group in groups.get("groups", []):
            versions = group.get("versions", [])
            if not versions:
                continue
            pref_ver = group.get("preferredVersion", {}).get("version")
            version = pref_ver if pref_ver else versions[0]["version"]
            group_name = group["name"]
            if group_name in ["metrics.k8s.io", "node.k8s.io"]:
                continue
            try:
                res_list = call_k8s_api(f"/apis/{group_name}/{version}")
                for res in res_list.get("resources", []):
                    if "list" in res.get("verbs", []):
                        item = (group_name, version, res["name"], res.get("namespaced", False), res.get("kind", ""))
                        (ns_list if item[3] else cluster_list).append(item)
            except Exception as e:
                logger.debug(f"Skip {group_name}/{version}: {e}")
    except Exception as e:
        logger.debug(f"Failed to list API groups: {e}")

    return ns_list, cluster_list


# === Slack notification ===
def send_slack_notification(
    slack_url: str,
    channel: str,
    cluster_name: str,
    success: bool,
    error_message: Optional[str] = None,
    duration: Optional[float] = None
):
    if not slack_url:
        return
    
    color = "#36a64f" if success else "#dc3545"
    
    if success:
        if duration is not None:
            text = f"kube-dump backup on {cluster_name} completed in {duration:.1f}s"
        else:
            text = f"kube-dump backup on {cluster_name} is success"
    else:
        text = f"kube-dump backup on {cluster_name} is failed"
    
    fields = []
    if not success and duration is not None:
        fields.append({
            "title": "Duration",
            "value": f"{duration:.1f}s",
            "short": True
        })
    if error_message:
        fields.append({
            "title": "Error",
            "value": f"```{error_message[:500]}```",
            "short": False
        })
    
    payload = {
        "channel": channel,
        "username": "kube-dump",
        "icon_emoji": ":kubernetes:",
        "attachments": [
            {
                "color": color,
                "text": text,
                "fields": fields,
                "ts": int(time.time())
            }
        ]
    }
    
    try:
        resp = requests.post(slack_url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Slack notification failed: {resp.status_code} {resp.text}")
        else:
            logger.info(f"Slack notification sent: {'success' if success else 'failed'}")
    except Exception as e:
        logger.warning(f"Failed to send Slack notification: {e}")


# === Git ops ===
class GitError(Exception):
    """Raised when git operations fail"""
    pass


def git_init_and_pull(
    repo_path: Path,
    branch: str,
    remote_url: Optional[str],
):
    """Initialize/clone repo and pull latest changes, then clean old backup folders."""
    if not remote_url:
        return None
    
    git_dir = repo_path / ".git"
    repo_path.mkdir(parents=True, exist_ok=True)
    
    if git_dir.exists():
        repo = Repo(repo_path)
        logger.info(f"Using existing git repo in {repo_path}")
    else:
        repo = Repo.init(repo_path)
        logger.info(f"Initialized new git repo in {repo_path}")
    
    # Try to pull from remote
    try:
        repo.git.fetch(remote_url, branch)
        # Check if branch exists locally
        if branch not in [ref.name for ref in repo.branches]:
            # Create branch tracking remote
            repo.git.checkout("-b", branch, f"FETCH_HEAD")
        else:
            repo.git.checkout(branch)
            repo.git.reset("--hard", "FETCH_HEAD")
        logger.info(f"Pulled latest from remote (branch: {branch})")
    except Exception as e:
        logger.debug(f"Could not pull from remote (may be empty repo): {e}")
        # Just checkout/create the branch
        if branch not in [ref.name for ref in repo.branches]:
            repo.git.checkout("-b", branch)
        else:
            repo.git.checkout(branch)
    
    # Clean old backup folders (keep .git and archives)
    for item in repo_path.iterdir():
        if item.name.startswith(".git"):
            continue
        if item.name.startswith("backup-") and ".tar" in item.name:
            continue
        if item.is_dir():
            shutil.rmtree(item)
            logger.debug(f"Removed old folder: {item.name}")
    
    return repo


def git_commit_and_push(
    repo: Repo,
    repo_path: Path,
    branch: str,
    remote_url: str,
    commit_user: str,
    commit_email: str,
    push: bool
):
    """Commit changes and push to remote."""
    if repo.is_dirty(untracked_files=True):
        repo.git.add(all=True)
        author = f"{commit_user} <{commit_email}>"
        repo.git.commit(message=f"Backup {datetime.now(timezone.utc).isoformat()}", author=author, no_verify=True)
        logger.info(f"Committing changes to git (branch: {branch})")
        if push:
            try:
                repo.git.push(remote_url, branch, set_upstream=True)
                logger.info("Pushing to origin")
            except Exception as e:
                raise GitError(f"Git push failed: {e}")
    else:
        logger.info("No changes to commit")


# === CLI ===
@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("command", required=False, type=click.Choice([
    "all", "dump", "ns", "dump-namespaces", "cls", "dump-cluster"
]))
@click.option("--silent", "-s", is_flag=True, envvar="SILENT")
@click.option("--destination-dir", "-d", type=click.Path(), default="./data", envvar="DESTINATION_DIR")
@click.option("--force-remove", "-f", is_flag=True, envvar="FORCE_REMOVE")
@click.option("--detailed", is_flag=True, envvar="DETAILED")
@click.option("--namespaces", "-n", default="", envvar="NAMESPACES")
@click.option("--namespaced-resources", "-r", default="", envvar="NAMESPACED_RESOURCES")
@click.option("--cluster-resources", "-k", default="", envvar="CLUSTER_RESOURCES")
@click.option("--kube-config", default=None, envvar="KUBE_CONFIG")
@click.option("--kube-context", default=None, envvar="KUBE_CONTEXT")
@click.option("--git-commit", "-c", is_flag=True, envvar="GIT_COMMIT")
@click.option("--git-push", "-p", is_flag=True, envvar="GIT_PUSH")
@click.option("--git-branch", "-b", default="main", envvar="GIT_BRANCH")
@click.option("--git-commit-user", default="kube-dump", envvar="GIT_COMMIT_USER")
@click.option("--git-commit-email", default="kube-dump@example.com", envvar="GIT_COMMIT_EMAIL")
@click.option("--git-remote-url", default=None, envvar="GIT_REMOTE_URL")
@click.option("--archive", "-a", is_flag=True, envvar="ARCHIVE")
@click.option("--archive-rotate-days", default=7, envvar="ARCHIVE_ROTATE_DAYS")
@click.option("--archive-type", type=click.Choice(["gz", "bz2", "xz"]), default="gz", envvar="ARCHIVE_TYPE")
@click.option("--cluster-name", default="unknown", envvar="CLUSTER_NAME")
@click.option("--slack-url", default=None, envvar="SLACK_URL")
@click.option("--slack-channel", default="#alerts", envvar="SLACK_CHANNEL")
def cli(
    command,
    silent,
    destination_dir,
    force_remove,
    detailed,
    namespaces,
    namespaced_resources,
    cluster_resources,
    kube_config,
    kube_context,
    git_commit,
    git_push,
    git_branch,
    git_commit_user,
    git_commit_email,
    git_remote_url,
    archive,
    archive_rotate_days,
    archive_type,
    cluster_name,
    slack_url,
    slack_channel
):
    global logger
    logger = setup_logger(silent=silent, with_timestamp=False)
    start_time = time.time()
    
    try:
        duration = _run_backup(
            command=command,
            destination_dir=destination_dir,
            force_remove=force_remove,
            detailed=detailed,
            namespaces=namespaces,
            namespaced_resources=namespaced_resources,
            cluster_resources=cluster_resources,
            kube_config=kube_config,
            kube_context=kube_context,
            git_commit=git_commit,
            git_push=git_push,
            git_branch=git_branch,
            git_commit_user=git_commit_user,
            git_commit_email=git_commit_email,
            git_remote_url=git_remote_url,
            archive=archive,
            archive_rotate_days=archive_rotate_days,
            archive_type=archive_type,
            start_time=start_time,
        )
        
        # Send success notification
        send_slack_notification(
            slack_url=slack_url,
            channel=slack_channel,
            cluster_name=cluster_name,
            success=True,
            duration=duration
        )
        
    except SystemExit as e:
        # Capture sys.exit() calls
        duration = time.time() - start_time
        if e.code != 0:
            send_slack_notification(
                slack_url=slack_url,
                channel=slack_channel,
                cluster_name=cluster_name,
                success=False,
                error_message="Backup failed (see logs)",
                duration=duration
            )
        raise
        
    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"Backup failed: {e}")
        
        # Send failure notification
        send_slack_notification(
            slack_url=slack_url,
            channel=slack_channel,
            cluster_name=cluster_name,
            success=False,
            error_message=error_msg,
            duration=duration
        )
        sys.exit(1)


def _run_backup(
    command,
    destination_dir,
    force_remove,
    detailed,
    namespaces,
    namespaced_resources,
    cluster_resources,
    kube_config,
    kube_context,
    git_commit,
    git_push,
    git_branch,
    git_commit_user,
    git_commit_email,
    git_remote_url,
    archive,
    archive_rotate_days,
    archive_type,
    start_time,
):
    mode_map = {"all": "all", "dump": "all", "ns": "ns", "dump-namespaces": "ns", "cls": "cls", "dump-cluster": "cls"}
    mode = mode_map.get(command, "all") if command else "all"

    dest = Path(destination_dir).resolve()
    
    # === Git init and pull first (cleans old folders) ===
    git_repo = None
    if git_commit and git_remote_url:
        git_repo = git_init_and_pull(
            repo_path=dest,
            branch=git_branch,
            remote_url=git_remote_url,
        )
    elif force_remove and dest.exists():
        shutil.rmtree(dest)
    
    dest.mkdir(parents=True, exist_ok=True)

    # Load kubeconfig
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config(config_file=kube_config, context=kube_context)
        except Exception as e:
            logger.error(f"Failed to load kubeconfig: {e}")
            sys.exit(1)

    # Resolve namespaces
    if namespaces.strip():
        ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    else:
        try:
            v1 = client.CoreV1Api()
            ns_list = [ns.metadata.name for ns in v1.list_namespace().items]
        except ApiException as e:
            logger.error(f"Failed to list namespaces: {e}")
            sys.exit(1)

    # Discover resources
    if namespaced_resources.strip() or cluster_resources.strip():
        logger.warning("Manual resource lists not supported in auto-discovery mode. Ignoring.")
    ns_resources, cls_resources = discover_resources()

    # === Dump namespaced ===
    if mode in ("all", "ns"):
        logger.info("Dumping namespaced resources")
        for ns in ns_list:
            for (group, version, res_name, _, kind) in ns_resources:
                try:
                    if group == "":
                        path = f"/api/{version}/namespaces/{ns}/{res_name}"
                        api_version = version
                    else:
                        path = f"/apis/{group}/{version}/namespaces/{ns}/{res_name}"
                        api_version = f"{group}/{version}"
                    data = call_k8s_api(path)
                    for item in data.get("items", []):
                        # Add apiVersion and kind (not included in list items)
                        item["apiVersion"] = api_version
                        item["kind"] = kind
                        cleaned = clean_resource(item, detailed=detailed)
                        save_object(cleaned, dest, res_name, namespace=ns)
                    logger.info(f"Dumping {res_name} from namespace={ns}")
                except ApiException as e:
                    if e.status == 403:
                        logger.warning(f"Access denied to {res_name} in namespace={ns}")
                    elif e.status == 404:
                        continue
                    else:
                        logger.debug(f"API error for {res_name}: {e}")
                except Exception as e:
                    logger.debug(f"Failed to dump {res_name} in {ns}: {e}")

    # === Dump cluster ===
    if mode in ("all", "cls"):
        logger.info("Dumping cluster-wide resources")
        for (group, version, res_name, _, kind) in cls_resources:
            try:
                if group == "":
                    path = f"/api/{version}/{res_name}"
                    api_version = version
                else:
                    path = f"/apis/{group}/{version}/{res_name}"
                    api_version = f"{group}/{version}"
                data = call_k8s_api(path)
                for item in data.get("items", []):
                    # Add apiVersion and kind (not included in list items)
                    item["apiVersion"] = api_version
                    item["kind"] = kind
                    cleaned = clean_resource(item, detailed=detailed)
                    save_object(cleaned, dest, res_name, namespace=None)
                logger.info(f"Dumping cluster-wide resource: {res_name}")
            except ApiException as e:
                if e.status == 403:
                    logger.warning(f"Access denied to {res_name}")
                elif e.status == 404:
                    continue
                else:
                    logger.debug(f"API error for {res_name}: {e}")
            except Exception as e:
                logger.debug(f"Failed to dump {res_name}: {e}")

    # === Archive in root ===
    if archive:
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_name = f"backup-{now}.tar.{archive_type}"
        archive_path = dest / archive_name

        tar_path = dest / f"backup-{now}.tar"
        with tarfile.open(tar_path, "w") as tar:
            for item in dest.iterdir():
                if item.name.startswith("backup-") and (".tar" in item.name or item.name.endswith(f".{archive_type}")):
                    continue
                tar.add(item, arcname=item.name)

        if archive_type == "gz":
            with open(tar_path, "rb") as f_in, gzip.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        elif archive_type == "bz2":
            with open(tar_path, "rb") as f_in, bz2.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        elif archive_type == "xz":
            with open(tar_path, "rb") as f_in, lzma.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        tar_path.unlink()
        logger.info(f"Archiving to {archive_path}")

        cutoff = datetime.now() - timedelta(days=archive_rotate_days)
        for arch in dest.glob(f"backup-*.tar.{archive_type}"):
            if arch != archive_path and arch.stat().st_mtime < cutoff.timestamp():
                arch.unlink()
                logger.info(f"Rotated old archive: {arch.name}")

    # === Git commit and push ===
    if git_commit and git_repo and git_remote_url:
        git_commit_and_push(
            repo=git_repo,
            repo_path=dest,
            branch=git_branch,
            remote_url=git_remote_url,
            commit_user=git_commit_user,
            commit_email=git_commit_email,
            push=git_push
        )

    duration = time.time() - start_time
    logger.info(f"Backup completed in {duration:.1f}s")
    return duration


if __name__ == "__main__":
    cli()