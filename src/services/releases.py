import asyncio
import html
import logging
import os
import re
import time
from urllib.parse import quote, urlparse

import bleach
import httpx
import markdown

from config import ReleaseSourceConfig

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
CODEBERG_NOTES_BRANCH = "forgejo/release-notes-published"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")


def normalize_version(version: str) -> str: return version.strip().removeprefix("refs/tags/").removeprefix("v")


def normalize_project(provider: str, project: str) -> str:
    value = (project or "").strip().rstrip("/")
    if not value:
        raise ValueError("Release source project is empty")
    if "://" in value:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        provider = provider.lower()
        if provider == "github" and host not in {"github.com", "www.github.com", "api.github.com"}:
            raise ValueError(f"Project URL {value!r} is not a GitHub repository URL")
        if provider == "codeberg" and host not in {"codeberg.org", "www.codeberg.org"}:
            raise ValueError(f"Project URL {value!r} is not a Codeberg repository URL")
        if provider == "gitlab" and not (host == "gitlab.com" or host.endswith(".gitlab.com")) and not path:
            raise ValueError(f"Invalid GitLab project URL: {value!r}")
        value = path
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if provider.lower() in {"github", "codeberg"}:
        parts = value.split("/")
        if len(parts) > 2:
            value = "/".join(parts[:2])
        if len(value.split("/")) != 2:
            raise ValueError(f"Invalid {provider} project {project!r}; expected 'owner/repository' or a repository URL")
    return value


def version_parts(version: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.fullmatch(normalize_version(version))
    return tuple(map(int, match.groups())) if match else None


def render_markdown(text: str) -> str:
    if not text:
        return ""
    rendered = markdown.markdown(html.unescape(text).strip(), extensions=["extra", "sane_lists", "nl2br"])
    return bleach.clean(
        rendered,
        tags={"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "ul", "ol", "li", "blockquote", "pre", "code", "a", "table", "thead", "tbody", "tr", "th", "td"},
        attributes={"a": ["href", "title"], "code": ["class"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


async def request_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int = 3,
    max_wait_seconds: int = 60,
    **kwargs,
) -> httpx.Response:
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(max_attempts):
        try:
            response = await client.get(url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == max_attempts - 1:
                raise
            delay = min(2**attempt, max_wait_seconds)
            logger.warning("HTTP network error; retrying in %ss: %s", delay, url)
            await asyncio.sleep(delay)
            continue
        rate_limited = False
        delay = min(2**attempt, max_wait_seconds)
        if response.status_code in {403, 429}:
            try:
                message = str(response.json().get("message", ""))
            except (ValueError, AttributeError):
                message = ""
            rate_limited = response.status_code == 429 or "rate limit" in message.lower() or "secondary rate" in message.lower()
            if rate_limited:
                retry_after = response.headers.get("Retry-After")
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset")
                if retry_after:
                    try:
                        delay = max(0, int(retry_after))
                    except ValueError:
                        pass
                elif remaining == "0" and reset:
                    try:
                        delay = max(0, int(reset) - int(time.time()))
                    except ValueError:
                        delay = max(60, delay)
                else:
                    delay = max(60, delay)
        should_retry = response.status_code in retryable_statuses or rate_limited
        if not should_retry:
            return response
        if attempt == max_attempts - 1 or delay > max_wait_seconds:
            logger.warning("HTTP request not retried further (status=%s, delay=%ss): %s", response.status_code, delay, url)
            return response
        logger.warning("HTTP %s; retry %d/%d in %ss: %s", response.status_code, attempt + 1, max_attempts - 1, delay, url)
        await asyncio.sleep(delay)
    raise RuntimeError("Unexpected retry loop termination")


def _github_error(response: httpx.Response, project: str) -> str:
    try:
        detail = response.json().get("message", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    if response.status_code == 404:
        return f"GitHub returned 404 for repository {project!r}. Check that the Release Source project uses the 'owner/repository' format (e.g., 'zabbix/zabbix') rather than a path with additional segments. A 404 may also indicate a private repository without a valid token. API details: {detail}"
    return f"GitHub API error ({response.status_code}) for {project!r}: {detail}"


async def fetch_codeberg_releases(client: httpx.AsyncClient, source: ReleaseSourceConfig) -> list[dict]:
    api_url = (source.api_url or "https://codeberg.org/api/v1").rstrip("/")
    project = normalize_project("codeberg", source.project)
    branch = getattr(source, "branch", None) or CODEBERG_NOTES_BRANCH
    headers = {"Accept": "application/json"}

    if source.token:
        headers["Authorization"] = f"token {source.token}"

    contents_url = f"{api_url}/repos/{project}/contents"
    response = await request_with_retry(client, contents_url, params={"ref": branch}, headers=headers, timeout=30.0)
    response.raise_for_status()
    items = response.json()

    if not isinstance(items, list):
        raise ValueError(f"Unexpected Codeberg contents response for {project}")
    results = []

    for item in items:
        if item.get("type") != "file":
            continue
        filename = item.get("name", "")
        match = re.fullmatch(r"(\d+\.\d+\.\d+)\.md", filename)
        if not match:
            continue
        raw_url = item.get("download_url")
        if not raw_url:
            raw_url = f"{api_url}/repos/{project}/raw/{quote(filename, safe='')}"
            raw_params = {"ref": branch}
        else:
            raw_params = None
        raw_response = await request_with_retry(client, raw_url, params=raw_params, headers=headers, timeout=30.0)
        raw_response.raise_for_status()
        version = match.group(1)
        results.append({
            "version": version,
            "name": f"Forgejo {version}",
            "published_at": None,
            "url": f"https://codeberg.org/{project}/src/branch/{quote(branch, safe='/')}/{quote(filename, safe='')}",
            "body": render_markdown(raw_response.text),
            "prerelease": False,
        })
    results.sort(key=lambda item: version_parts(item["version"]) or (0, 0, 0), reverse=True)
    return results


async def fetch_releases(client: httpx.AsyncClient, source: ReleaseSourceConfig) -> list[dict]:
    provider = (source.provider or "").lower().strip()

    if provider == "codeberg":
        return await fetch_codeberg_releases(client, source)
    
    project = normalize_project(provider, source.project)
    headers = {"Accept": "application/vnd.github+json" if provider == "github" else "application/json"}

    if provider == "github":
        token = source.token or GITHUB_TOKEN

        if token:
            headers["Authorization"] = f"Bearer {token}"

        headers["X-GitHub-Api-Version"] = "2022-11-28"
        api_url = (source.api_url or "https://api.github.com").rstrip("/")
        url = f"{api_url}/repos/{project}/releases"

    elif provider == "gitlab":
        api_url = (source.api_url or "https://gitlab.com/api/v4").rstrip("/")
        url = f"{api_url}/projects/{quote(project, safe='')}/releases"
        if source.token:
            headers["PRIVATE-TOKEN"] = source.token
    else:
        raise ValueError(f"Unsupported release provider: {provider}")
    
    results = []
    page = 1
    while True:
        response = await request_with_retry(client, url, params={"per_page": 100, "page": page}, headers=headers, timeout=30.0)

        if provider == "github" and response.status_code >= 400:
            raise RuntimeError(_github_error(response, project))
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            raise ValueError(f"Unexpected releases API response for {provider}/{project}")
        
        if not data:
            break

        for item in data:
            if provider == "github":

                if item.get("draft") or item.get("prerelease"):
                    continue
                tag = item.get("tag_name") or ""
                release = {
                    "version": tag,
                    "name": item.get("name") or tag,
                    "published_at": item.get("published_at"),
                    "url": item.get("html_url") or f"https://github.com/{project}/releases",
                    "body": render_markdown(item.get("body") or ""),
                    "prerelease": bool(item.get("prerelease", False)),
                }

            else:
                tag = item.get("tag_name") or ""
                web_url = api_url.removesuffix("/api/v4")
                release = {
                    "version": tag,
                    "name": item.get("name") or tag,
                    "published_at": item.get("released_at"),
                    "url": f"{web_url}/{project}/-/releases/{quote(tag, safe='')}",
                    "body": render_markdown(item.get("description") or ""),
                    "prerelease": False,
                }

            results.append(release)

        if len(data) < 100:
            break
        page += 1
    return results


def filter_obsolete_patch_releases(releases: list[dict], previous_version: str) -> list[dict]:
    previous_parts = version_parts(previous_version)

    if previous_parts is None:
        return releases
    
    previous_major, previous_minor, _ = previous_parts
    boundaries = []
    
    for release in releases:
        parts = version_parts(release["version"])

        if parts is None:
            continue
        major, minor, patch = parts

        if (major, minor) > (previous_major, previous_minor) and patch == 0:
            boundaries.append((major, minor))

    if not boundaries:
        return releases
    
    first_boundary = min(boundaries)

    return [release for release in releases if (parts := version_parts(release["version"])) is None or parts[:2] >= first_boundary]


async def get_release_notes_between(client: httpx.AsyncClient, source: ReleaseSourceConfig, previous_version: str, latest_version: str) -> list[dict]:
    previous_tag = normalize_version(previous_version)
    latest_tag = normalize_version(latest_version)

    if previous_tag == latest_tag:
        return []
    
    releases = await fetch_releases(client, source)
    indexes = {}

    for index, release in enumerate(releases):
        indexes.setdefault(normalize_version(release["version"]), index)

    if previous_tag not in indexes:
        raise ValueError(f"Release {previous_version} not found in {source.provider}/{source.project}")
    
    if latest_tag not in indexes:
        raise ValueError(f"Release {latest_version} not found in {source.provider}/{source.project}")
    
    previous_index = indexes[previous_tag]
    latest_index = indexes[latest_tag]

    if latest_index > previous_index:
        raise ValueError(f"Release order is invalid: {previous_version} -> {latest_version}")
    
    selected = list(reversed(releases[latest_index:previous_index]))
    selected = filter_obsolete_patch_releases(selected, previous_version)
    logger.info("Release Notes: %s/%s: %s -> %s (%d releases)", source.provider, source.project, previous_version, latest_version, len(selected))

    return selected

