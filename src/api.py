from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_alerts_count": len(
                get_active_alerts()
            ),
        },
    )


@app.get(
    "/alerts",
    response_class=HTMLResponse,
)
async def alerts_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": get_active_alerts(),
        },
    )


@app.get(
    "/releases",
    response_class=HTMLResponse,
)
async def releases_page(
    request: Request,
):
    return templates.TemplateResponse(
        request,
        "releases.html",
        {},
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

    return templates.TemplateResponse(
        request,
        "artifacthub_chart.html",
        {
            "package": package,
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