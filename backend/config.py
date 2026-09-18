import hashlib
import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from backend.embedding_spec import BGE_MODEL, BGE_REVISION, BGE_PIPELINE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./data/workspace.db"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    job_mode: str = "local"
    allow_demo_publication: bool | None = None  # Retired input is rejected, never acted on.
    model_provider: str = "openai"
    chat_model: str = "gpt-5.6-luna"
    grader_model: str = "gpt-5.6-terra"
    answer_reasoning_effort: str = "low"
    agent_reasoning_effort: str = "medium"
    grader_reasoning_effort: str = "medium"
    model_max_output_tokens: int = Field(default=4096, ge=512, le=16000)
    provider_timeout_seconds: int = Field(default=45, ge=1, le=120)
    embedding_provider: str = "auto"
    embedding_model: str = BGE_MODEL
    embedding_dimensions: int = 384
    embedding_model_dir: Path = Path("data/models/bge-small-en-v1.5")
    openai_api_key: str = ""
    app_origin: str = "http://localhost:5173"
    cookie_secure: bool = False
    bootstrap_email: str = "admin@example.test"
    bootstrap_password: str = ""
    retired_demo_password: str = ""
    session_hours: int = 12
    max_upload_mb: int = 20
    run_deadline_seconds: int = 60
    run_token_budget: int = 16000
    trace_retention_days: int = 30
    max_job_attempts: int = Field(default=3, ge=1, le=10)
    grader_calibration_file: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_picker_api_key: str = ""
    google_project_number: str = ""
    connection_encryption_key: str = ""
    mcp_signing_key: str = ""
    gmail_mcp_url: str = "http://gmail-mcp:8101/mcp"
    drive_mcp_url: str = "http://drive-mcp:8102/mcp"
    searxng_url: str = ""

    def validate_deployment(self):
        if self.allow_demo_publication is not None:
            raise ValueError("Remove retired ALLOW_DEMO_PUBLICATION from configuration; demo mode is removed")
        embedding = self.embedding_profile()
        if embedding["embedding_provider"] == "local":
            if self.embedding_model != BGE_MODEL or self.embedding_dimensions != 384:
                raise ValueError("Local embeddings require BAAI/bge-small-en-v1.5 and 384 dimensions")
        elif embedding["embedding_provider"] == "openai":
            if self.embedding_model != "text-embedding-3-small" or self.embedding_dimensions != 1536:
                raise ValueError(
                    "Legacy OpenAI embeddings require text-embedding-3-small and 1536 dimensions"
                )
        else:
            raise ValueError(
                "EMBEDDING_PROVIDER must be local or legacy openai; synthetic inference is retired"
            )
        for effort in (
            self.answer_reasoning_effort,
            self.agent_reasoning_effort,
            self.grader_reasoning_effort,
        ):
            if effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
                raise ValueError("Unsupported reasoning effort")
        if self.model_provider != "openai":
            raise ValueError("MODEL_PROVIDER must be openai; demo mode has been removed")
        if self.job_mode not in {"local", "celery"}:
            raise ValueError("JOB_MODE must be local or celery")
        if (
            self.model_provider == "openai" or embedding["embedding_provider"] == "openai"
        ) and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
        if self.app_env == "production":
            if not self.cookie_secure or not self.app_origin.startswith("https://"):
                raise ValueError("Production requires HTTPS and COOKIE_SECURE=true")
            if not self.database_url.startswith("postgresql") or self.job_mode != "celery":
                raise ValueError("Production requires PostgreSQL and Celery")
            # Administrators can calibrate in-app after startup. Publication remains gated.
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if embedding["embedding_provider"] == "local":
            from backend.embeddings import embedding_provider

            embedding_provider(self.model_profile())  # Check the offline artifact before accepting requests.

    def grader_identity(self):
        return {
            "provider": self.model_provider,
            "grader_model": self.grader_model,
            "grader_revision": "grounded-grader-v1",
            "reasoning_effort": self.grader_reasoning_effort,
            "max_output_tokens": self.model_max_output_tokens,
        }

    def embedding_profile(self):
        provider = self.embedding_provider
        if provider == "auto":
            provider = "local"
        return {
            "embedding_provider": provider,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
            "embedding_revision": BGE_REVISION if provider == "local" else "legacy-v1",
            "embedding_pipeline": BGE_PIPELINE if provider == "local" else "legacy-v1",
        }

    def calibration_valid(self):
        try:
            value = json.loads(Path(self.grader_calibration_file).read_text(encoding="utf-8"))
            metrics = value["metrics"]
            return (
                value["passed"] is True
                and value["grader_identity"] == self.grader_identity()
                and bool(value["reviewed_by"])
                and bool(value["reviewed_at"])
                and metrics["examples"] >= 10
                and metrics["correctness_mae"] <= 0.1
                and metrics["evidence_support_mae"] <= 0.1
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def model_profile(self) -> dict:
        calibration_hash = "unavailable"
        if self.grader_calibration_file and self.calibration_valid():
            calibration_hash = hashlib.sha256(Path(self.grader_calibration_file).read_bytes()).hexdigest()
        return {
            "provider": self.model_provider,
            "chat_model": self.chat_model,
            "grader_model": self.grader_model,
            **self.embedding_profile(),
            "answer_reasoning_effort": self.answer_reasoning_effort,
            "agent_reasoning_effort": self.agent_reasoning_effort,
            "grader_reasoning_effort": self.grader_reasoning_effort,
            "model_max_output_tokens": self.model_max_output_tokens,
            "provider_timeout_seconds": self.provider_timeout_seconds,
            "execution_revision": "relay-engine-v3",
            "grader_revision": self.grader_identity()["grader_revision"],
            "grader_calibration_sha256": calibration_hash,
        }


@lru_cache
def settings() -> Settings:
    return Settings()
