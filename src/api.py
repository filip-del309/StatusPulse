from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import bleach
import markdown

from markupsafe import Markup

app = FastAPI()

templates = Jinja2Templates(
    directory="templates"
)

artifacthub_source = None
active_alerts = {}


def set_artifacthub_source(source):
    global artifacthub_source

    artifacthub_source = source


def set_active_alert(source: str, alert: dict) -> None:
    source_alerts = active_alerts.setdefault(source, {})

    source_alerts[alert["id"]] = alert


def remove_active_alert(source: str, alert_id: str) -> None:
    source_alerts = active_alerts.get(source, {})

    source_alerts.pop(alert_id, None)


def get_active_alerts() -> list[dict]:
    alerts = []

    for source_alerts in active_alerts.values():
        alerts.extend(source_alerts.values())

    alerts.sort(key=lambda alert: alert.get("updated_at", ""), reverse=True)

    return alerts


def get_outdated_charts_count() -> int:
    if artifacthub_source is None:
        return 0

    outdated_charts = set()

    for package in artifacthub_source.packages_data.values():
        latest_version = package.get("version")
        repository_versions = package.get("repository_versions", {})

        if not latest_version:
            continue

        for used_version in repository_versions.values():
            if used_version and used_version != latest_version:
                outdated_charts.add(package["chart"])
                break

    return len(outdated_charts)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_alerts_count": len(
                get_active_alerts()
            ),
            "outdated_charts_count": get_outdated_charts_count(),
        },
    )


@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": get_active_alerts(),
        },
    )


@app.get("/releases", response_class=HTMLResponse)
async def releases_page(request: Request):
    if artifacthub_source is None:
        raise HTTPException(
            status_code=503,
            detail="ArtifactHub source is not initialized",
        )

    packages = artifacthub_source.storage.get_release_notes()

    packages.sort(
        key=lambda item: (
            item.get("chart", item.get("package", "")).lower(),
            item.get("folder", "").lower(),
        )
    )

    return templates.TemplateResponse(
        request,
        "releases.html",
        {"packages": packages},
    )


@app.get(
    "/releases/package/{package_name:path}",
    response_class=HTMLResponse,
)
async def package_release_details_page(
    request: Request,
    package_name: str,
):
    if artifacthub_source is None:
        raise HTTPException(
            status_code=503,
            detail="ArtifactHub source is not initialized",
        )

    package = next(
        (
            item
            for item in artifacthub_source.storage.get_release_notes()
            if item.get("package") == package_name
        ),
        None,
    )

    if package is None:
        raise HTTPException(
            status_code=404,
            detail="Release notes not found",
        )

    return templates.TemplateResponse(
        request,
        "release_details.html",
        {
            "package": package,
        },
    )


@app.get("/releases/{chart_name}", response_class=HTMLResponse)
async def release_details_page(request: Request, chart_name: str):
    if artifacthub_source is None:
        raise HTTPException(
            status_code=503,
            detail="ArtifactHub source is not initialized",
        )

    packages = artifacthub_source.storage.get_release_notes()

    package = next(
        (
            item
            for item in packages
            if item.get("folder") == chart_name
            or item.get("chart") == chart_name
        ),
        None,
    )

    if package is None:
        raise HTTPException(
            status_code=404,
            detail="Release notes not found",
        )

    return templates.TemplateResponse(
        request,
        "release_details.html",
        {
            "package": package,
        },
    )

@app.get(
    "/artifacthub/{chart_name}",
    response_class=HTMLResponse,
)
async def artifacthub_chart_status(
    request: Request,
    chart_name: str,
):
    if artifacthub_source is None:
        raise HTTPException(
            status_code=503,
            detail="ArtifactHub source is not initialized",
        )

    package = next(
        (
            package
            for package in artifacthub_source.packages_data.values()
            if package["chart"] == chart_name
        ),
        None,
    )

    if package is None:
        raise HTTPException(
            status_code=404,
            detail=f"Chart '{chart_name}' not found",
        )

    upgrade_guide = None

    previous_version = package.get(
        "upgrade_guide_previous_version"
    )

    latest_version = package.get("upgrade_guide_latest_version")

    if previous_version and latest_version:
        stored_guide = artifacthub_source.storage.get_upgrade_guide(
            package["package"],
            previous_version,
            latest_version,
        )

        if stored_guide is not None:
            rendered_html = markdown.markdown(
                stored_guide["guide"],
                extensions=[
                    "fenced_code",
                    "tables",
                ],
            )

            safe_html = bleach.clean(
                rendered_html,
                tags={
                    "h1", "h2", "h3", "h4",
                    "p", "br", "hr",
                    "strong", "em", "del",
                    "ul", "ol", "li",
                    "blockquote",
                    "pre", "code",
                    "a",
                    "table", "thead", "tbody",
                    "tr", "th", "td",
                },
                attributes={
                    "a": ["href", "title"],
                },
                protocols=["http", "https"],
                strip=True,
            )

            upgrade_guide = {
                **stored_guide,
                "html": Markup(safe_html),
            }

    return templates.TemplateResponse(
        request,
        "artifacthub_chart.html",
        {
            "package": package,
            "upgrade_guide": upgrade_guide,
        },
    )


@app.get(
    "/artifacthub",
    response_class=HTMLResponse,
)
async def artifacthub_status(
    request: Request,
):
    if artifacthub_source is None:
        raise HTTPException(
            status_code=503,
            detail="ArtifactHub source is not initialized",
        )

    packages = list(
        artifacthub_source.packages_data.values()
    )

    packages.sort(
        key=lambda package: package["chart"].lower()
    )

    return templates.TemplateResponse(
        request,
        "artifacthub.html",
        {
            "packages": packages,
        },
    )