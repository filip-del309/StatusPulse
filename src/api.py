from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


app = FastAPI()

templates = Jinja2Templates(
    directory="templates"
)

artifacthub_source = None


def set_artifacthub_source(source):
    global artifacthub_source

    artifacthub_source = source


@app.get(
    "/status/artifacthub/{chart_name}",
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
    "/status/artifacthub",
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
