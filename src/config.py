from dataclasses import dataclass
from pathlib import Path
import os

import yaml
from dotenv import load_dotenv


load_dotenv()


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class ScheduleConfig:
    type: str
    interval: int | None = None
    cron: str | None = None
    timezone: str = "UTC"


@dataclass
class TailscaleFilterConfig:
    components_enabled: bool
    components: list[str]


@dataclass
class TailscaleMessageConfig:
    title: bool
    status: bool
    components: bool
    updated_at: bool
    link: bool


@dataclass
class TailscaleConfig:
    enabled: bool
    schedule: ScheduleConfig
    filters: TailscaleFilterConfig
    message: TailscaleMessageConfig


@dataclass
class CloudflareFilterConfig:
    required_matches: int
    severity_enabled: bool
    severities: list[str]
    components_enabled: bool
    components: list[str]


@dataclass
class CloudflareMessageConfig:
    title: bool
    severity: bool
    status: bool
    components: bool
    updated_at: bool
    link: bool


@dataclass
class CloudflareConfig:
    enabled: bool
    schedule: ScheduleConfig
    filters: CloudflareFilterConfig
    message: CloudflareMessageConfig


@dataclass
class ScalewayConfig:
    enabled: bool
    schedule: ScheduleConfig


@dataclass
class ArtifactHubRepositoryConfig:
    url: str
    branch: str
    path: str
    username: str
    token: str


@dataclass
class ArtifactHubPackageConfig:
    name: str
    folders: list[str]


@dataclass
class ArtifactHubConfig:
    enabled: bool
    schedule: ScheduleConfig
    lookback_hours: int
    packages: list[ArtifactHubPackageConfig]
    statuspulse_url: str
    repository: ArtifactHubRepositoryConfig


@dataclass
class SourcesConfig:
    tailscale: TailscaleConfig
    cloudflare: CloudflareConfig
    artifacthub: ArtifactHubConfig
    scaleway: ScalewayConfig


@dataclass
class Config:
    telegram: TelegramConfig
    sources: SourcesConfig


def load_schedule(data: dict) -> ScheduleConfig:
    return ScheduleConfig(
        type=data["type"],
        interval=data.get("interval"),
        cron=data.get("cron"),
        timezone=data.get("timezone", "UTC"),
    )


def load_tailscale_filters(data: dict) -> TailscaleFilterConfig:
    components_data = data.get("components", {})

    return TailscaleFilterConfig(
        components_enabled=components_data.get("enabled", False),
        components=components_data.get("values", [])
    )


def load_tailscale_message(data: dict) -> TailscaleMessageConfig:
    return TailscaleMessageConfig(
        title=data.get("title", True),
        status=data.get("status", True),
        components=data.get("components", True),
        updated_at=data.get("updated_at", True),
        link=data.get("link", True)
    )


def load_cloudflare_filters(data: dict) -> CloudflareFilterConfig:
    severity_data = data.get("severity", {})
    components_data = data.get("components", {})
    required_matches = data.get("required_matches", 1)

    return CloudflareFilterConfig(
        required_matches=required_matches,
        severity_enabled=severity_data.get("enabled", False),
        severities=severity_data.get("values", []),
        components_enabled=components_data.get("enabled", False),
        components=components_data.get("values", []),
    )


def load_cloudflare_message(data: dict) -> CloudflareMessageConfig:
    return CloudflareMessageConfig(
        title=data.get("title", True),
        severity=data.get("severity", True),
        status=data.get("status", True),
        components=data.get("components", True),
        updated_at=data.get("updated_at",True),
        link=data.get("link", True),
    )


def load_config() -> Config:
    config_path = Path(os.getenv("CONFIG_PATH", Path(__file__).parent.parent / "config.yaml"))

    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    telegram_data = data["telegram"]

    bot_token_name = (telegram_data["bot_token"].removeprefix("${").removesuffix("}"))
    chat_id_name = (telegram_data["chat_id"].removeprefix("${").removesuffix("}"))

    telegram = TelegramConfig(bot_token=os.environ[bot_token_name], chat_id=os.environ[chat_id_name])

    sources_data = data["sources"]

    tailscale_data = sources_data["tailscale"]

    tailscale = TailscaleConfig(
        enabled=tailscale_data.get("enabled", True,),
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
    )

    artifacthub_data = sources_data["artifacthub"]

    repository_data = artifacthub_data["repository"]

    github_username_name = (
        repository_data["username"]
        .removeprefix("${")
        .removesuffix("}")
    )

    github_token_name = (
        repository_data["token"]
        .removeprefix("${")
        .removesuffix("}")
    )

    repository = ArtifactHubRepositoryConfig(
        url=repository_data["url"],
        branch=repository_data.get("branch", "main"),
        path=repository_data["path"],
        username=os.environ[github_username_name],
        token=os.environ[github_token_name]
    )

    packages = [
        ArtifactHubPackageConfig(
            name=package["name"],
            folders=package.get("folders", []),
        )
        for package in artifacthub_data.get("packages", [])
    ]

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
    )
