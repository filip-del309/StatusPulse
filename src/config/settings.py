import os
from pathlib import Path

from dotenv import load_dotenv
from .loader import (
    load_cloudflare_filters,
    load_cloudflare_message,
    load_scaleway_filters,
    load_scaleway_message,
    load_schedule,
    load_tailscale_filters,
    load_tailscale_message,
)
from .models import (
    ArtifactHubConfig,
    ArtifactHubPackageConfig,
    ArtifactHubRepositoryConfig,
    CloudflareConfig,
    Config,
    DatabaseConfig,
    ReleaseSourceConfig,
    ScalewayConfig,
    SourcesConfig,
    TailscaleConfig,
    TelegramConfig,
    UpgradeAIConfig,
)
from .env import get_bool, get_env, get_float, get_int, get_path
from .yaml_loader import load_yaml_config


load_dotenv()


def load_config() -> Config:
    data = load_yaml_config()

    telegram = TelegramConfig(
        bot_token=get_env("TELEGRAM_BOT_TOKEN", required=True),
        chat_id=get_env("TELEGRAM_CHAT_ID", required=True),
    )

    sources_data = data["sources"]

    tailscale_data = sources_data["tailscale"]

    tailscale = TailscaleConfig(
        enabled=tailscale_data.get("enabled", True),
        schedule=load_schedule(tailscale_data["schedule"]),
        filters=load_tailscale_filters(tailscale_data.get("filters", {})),
        message=load_tailscale_message(tailscale_data.get("message", {}))
    )

    cloudflare_data = sources_data["cloudflare"]

    cloudflare = CloudflareConfig(
        enabled=cloudflare_data.get("enabled", True),
        schedule=load_schedule(cloudflare_data["schedule"]),
        filters=load_cloudflare_filters(cloudflare_data.get("filters", {})),
        message=load_cloudflare_message(cloudflare_data.get("message", {}))
    )

    scaleway_data = sources_data["scaleway"]

    scaleway = ScalewayConfig(
        enabled=scaleway_data.get(
            "enabled",
            True,
        ),
        schedule=load_schedule(
            scaleway_data["schedule"],
        ),
        filters=load_scaleway_filters(
            scaleway_data.get(
                "filters",
                {},
            ),
        ),
        message=load_scaleway_message(
            scaleway_data.get(
                "message",
                {},
            ),
        ),
    )

    artifacthub_data = sources_data["artifacthub"]

    repository_data = artifacthub_data["repository"]

    repository = ArtifactHubRepositoryConfig(
        url=repository_data["url"],
        branch=repository_data.get("branch", "main"),
        path=repository_data["path"],
        username=get_env("ARTIFACTHUB_GIT_REPOSITORY_USERNAME", required=True),
        token=get_env("ARTIFACTHUB_GIT_REPOSITORY_TOKEN", required=True)
    )

    packages = []

    for package in artifacthub_data.get("packages", []):
        releases_data = package.get("releases")

        release_source = None

        if releases_data:
            token = releases_data.get("token")

            if token:
                token_name = (
                    token.removeprefix("${")
                    .removesuffix("}")
                )
                token = get_env(token_name, required=True)

            provider = releases_data["provider"].lower()

            if provider not in {"github", "gitlab", "codeberg"}:
                raise ValueError(
                    f"Unsupported release provider: {provider}"
                )

            release_source = ReleaseSourceConfig(
                provider=provider,
                project=releases_data["project"],
                api_url=releases_data.get("api_url"),
                token=token,
            )

        packages.append(
            ArtifactHubPackageConfig(
                name=package["name"],
                folders=package.get("folders", []),
                releases=release_source,
            )
        )

    database_data = data.get("database", {})

    database = DatabaseConfig(
        path=Path(
            os.getenv(
                "STATUSPULSE_DB_PATH",
                database_data.get(
                    "artifacthub_path",
                    "./data/statuspulse.db",
                ),
            )
        ).expanduser(),
    )

    upgrade_ai = UpgradeAIConfig(
        enabled=get_bool("UPGRADE_AI_ENABLED", False),
        base_url=get_env("LLM_BASE_URL", "") or "",
        api_key=get_env("LLM_API_KEY"),
        model=get_env("LLM_MODEL", "") or "",
        timeout=get_float("LLM_TIMEOUT", 30.0),
        max_context_chars=get_int("LLM_MAX_CONTEXT_CHARS", 12000),
        max_readme_chars=get_int("LLM_MAX_README_CHARS", 8000),
        debug=get_bool("UPGRADE_AI_DEBUG", False),
        debug_dir=get_path("UPGRADE_AI_DEBUG_DIR", "./data/upgrade_ai_debug"),
        readme_only=get_bool("UPGRADE_AI_README_ONLY", False),
    )

    artifacthub = ArtifactHubConfig(
        enabled=artifacthub_data.get("enabled", True),
        schedule=load_schedule(artifacthub_data["schedule"]),
        lookback_hours=artifacthub_data.get("lookback_hours", 24),
        packages=packages,
        statuspulse_url=artifacthub_data.get("statuspulse_url", "http://localhost:8000"),
        repository=repository,
    )

    return Config(
        telegram=telegram,
        sources=SourcesConfig(
            tailscale=tailscale,
            cloudflare=cloudflare,
            artifacthub=artifacthub,
            scaleway=scaleway,
        ),
        database=database,
        upgrade_ai=upgrade_ai,
    )
