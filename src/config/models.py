from dataclasses import dataclass
from pathlib import Path

@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class ReleaseSourceConfig:
    provider: str
    project: str
    api_url: str | None = None
    token: str | None = None
    branch: str | None = None


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
class ScalewayFilterConfig:
    required_matches: int
    severity_enabled: bool
    severities: list[str]
    components_enabled: bool
    components: list[str]


@dataclass
class ScalewayMessageConfig:
    title: bool
    severity: bool
    status: bool
    components: bool
    updated_at: bool
    link: bool


@dataclass
class ScalewayConfig:
    enabled: bool
    schedule: ScheduleConfig
    filters: ScalewayFilterConfig
    message: ScalewayMessageConfig


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
    releases: ReleaseSourceConfig | None = None


@dataclass
class ArtifactHubConfig:
    enabled: bool
    schedule: ScheduleConfig
    lookback_hours: int
    packages: list[ArtifactHubPackageConfig]
    statuspulse_url: str
    repository: ArtifactHubRepositoryConfig


@dataclass
class DatabaseConfig:
    path: Path


@dataclass
class UpgradeAIConfig:
    enabled: bool
    base_url: str
    api_key: str | None
    model: str
    timeout: float
    max_context_chars: int
    max_readme_chars: int
    debug: bool
    debug_dir: Path
    readme_only: bool

    
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
    database: DatabaseConfig
    upgrade_ai: UpgradeAIConfig
