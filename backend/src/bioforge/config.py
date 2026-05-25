from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    db_url: str = Field(
        default="sqlite+aiosqlite:///./bioforge.db", alias="BIOFORGE_DB_URL"
    )
    default_model: str = Field(default="claude-sonnet-4-6", alias="BIOFORGE_DEFAULT_MODEL")
    default_project_id: str = Field(
        default="default-project", alias="BIOFORGE_DEFAULT_PROJECT_ID"
    )
    entrez_email: str = Field(default="", alias="BIOFORGE_ENTREZ_EMAIL")
    max_agent_iterations: int = Field(default=4, alias="BIOFORGE_MAX_AGENT_ITERATIONS")

    # OpenTelemetry — disabled by default so the test suite stays quiet. Enable via
    # BIOFORGE_OTEL_ENABLED=true. The exporter defaults to console; set
    # BIOFORGE_OTEL_EXPORTER=otlp + BIOFORGE_OTEL_ENDPOINT for real ingest.
    otel_enabled: bool = Field(default=False, alias="BIOFORGE_OTEL_ENABLED")
    otel_exporter: str = Field(
        default="console", alias="BIOFORGE_OTEL_EXPORTER"
    )  # console | none | otlp
    otel_endpoint: str = Field(
        default="http://localhost:4318/v1/traces", alias="BIOFORGE_OTEL_ENDPOINT"
    )
    otel_headers: str = Field(default="", alias="BIOFORGE_OTEL_HEADERS")


settings = Settings()
