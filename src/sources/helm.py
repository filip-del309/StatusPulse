import asyncio
import io
import subprocess
import tarfile
import tempfile
from pathlib import Path

import httpx
import yaml


async def get_version(
    client: httpx.AsyncClient,
    package_name: str,
    version: str,
) -> dict:
    url = (
        "https://artifacthub.io/api/v1/packages/helm/"
        f"{package_name}/{version}"
    )

    response = await client.get(url)
    response.raise_for_status()

    return response.json()


def download_oci_values(
    chart_url: str,
    version: str,
) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(
            [
                "helm",
                "pull",
                chart_url,
                "--version",
                version,
                "--destination",
                directory,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        chart_files = list(
            Path(directory).glob("*.tgz")
        )

        if not chart_files:
            raise RuntimeError(
                f"No Helm chart downloaded from {chart_url}"
            )

        with tarfile.open(
            chart_files[0],
            mode="r:gz",
        ) as tar:
            values_file = next(
                member
                for member in tar.getmembers()
                if member.name.endswith("/values.yaml")
            )

            content = tar.extractfile(values_file).read()

            return yaml.safe_load(content) or {}

        
async def download_values(
    client: httpx.AsyncClient,
    chart_url: str,
    version: str,
) -> dict:
    if chart_url.startswith("oci://"):
        return await asyncio.to_thread(
            download_oci_values,
            chart_url,
            version,
        )

    response = await client.get(
        chart_url,
        follow_redirects=True,
    )
    response.raise_for_status()

    with tarfile.open(
        fileobj=io.BytesIO(response.content),
        mode="r:gz",
    ) as tar:
        values_file = next(
            member
            for member in tar.getmembers()
            if member.name.endswith("/values.yaml")
        )

        content = tar.extractfile(values_file).read()

        return yaml.safe_load(content) or {}


def diff_dict(old, new, path=""):
    changes = []

    old = old or {}
    new = new or {}

    keys = set(old) | set(new)

    for key in sorted(keys):
        current_path = (
            f"{path}.{key}"
            if path
            else key
        )

        if key not in old:
            changes.append({
                "type": "added",
                "path": current_path,
                "new": new[key],
            })

        elif key not in new:
            changes.append({
                "type": "removed",
                "path": current_path,
                "old": old[key],
            })

        elif (
            isinstance(old[key], dict)
            and isinstance(new[key], dict)
        ):
            changes.extend(
                diff_dict(
                    old[key],
                    new[key],
                    current_path,
                )
            )

        elif old[key] != new[key]:
            changes.append({
                "type": "changed",
                "path": current_path,
                "old": old[key],
                "new": new[key],
            })

    return changes


async def get_values_diff(
    client: httpx.AsyncClient,
    package_name: str,
    previous_version: str,
    latest_version: str,
) -> list[dict]:
    latest = await get_version(
        client,
        package_name,
        latest_version,
    )

    previous = await get_version(
        client,
        package_name,
        previous_version,
    )

    latest_values = await download_values(
        client,
        latest["content_url"],
        latest_version,
    )

    previous_values = await download_values(
        client,
        previous["content_url"],
        previous_version,
    )

    return diff_dict(
        previous_values,
        latest_values,
    )