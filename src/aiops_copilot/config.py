"""Configuration loaded from a YAML file, with a few environment overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class LLMConfig(BaseModel):
    enabled: bool = True
    # Any OpenAI-compatible endpoint: Ollama, llama.cpp server, vLLM.
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen2.5:7b-instruct"
    api_key: str = "local"
    timeout_s: float = 180.0
    temperature: float = 0.1
    max_tokens: int = 800
    language: str = "en"


class CloudConfig(BaseModel):
    """Optional second opinion from a cloud model. Off by default; see routing.py."""

    enabled: bool = False
    # Any OpenAI-compatible endpoint the organisation has approved (in-country provider, Azure, ...).
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    api_key_env: str = "AIOPS_CLOUD_API_KEY"
    timeout_s: float = 60.0
    # Ask the cloud only when min(final confidence, evidence score) is below this.
    min_local_confidence: float = 0.6
    # Incidents from these namespaces never leave the organisation.
    local_only_namespaces: list[str] = Field(default_factory=list)
    # Alert label that marks data classification; these values never leave the organisation.
    classification_label: str = "data_classification"
    local_only_classifications: list[str] = Field(default_factory=lambda: ["restricted", "confidential"])


class PrometheusConfig(BaseModel):
    enabled: bool = True
    url: str = "http://localhost:9090"


class LokiConfig(BaseModel):
    enabled: bool = True
    url: str = "http://localhost:3100"


class KubernetesConfig(BaseModel):
    enabled: bool = True
    kubeconfig: str | None = None
    context: str | None = None


class GitOpsConfig(BaseModel):
    # Local clone of the GitOps repository the copilot proposes changes to.
    repo_path: str = "."
    manifests_dir: str = "gitops/apps"
    github_repo: str = "bayforza/aiops"
    base_branch: str = "main"
    dry_run: bool = True
    token_env: str = "GITHUB_TOKEN"
    remote: str = "origin"


class PolicyConfig(BaseModel):
    # 0 = analyse and notify only; 1 = may also open pull requests.
    # Executing changes directly (level 2+) is intentionally not implemented.
    autonomy_level: int = 0
    min_confidence: float = 0.6

    @field_validator("autonomy_level")
    @classmethod
    def _level(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("autonomy_level must be 0 (notify) or 1 (pull requests); level 2+ is not supported")
        return v


class ContextConfig(BaseModel):
    window_minutes: int = 30
    max_log_lines: int = 500
    # Character budget for the prompt context (~4 chars per token).
    budget_chars: int = 6000


class NotifyConfig(BaseModel):
    webhook_url: str | None = None


class Settings(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    loki: LokiConfig = Field(default_factory=LokiConfig)
    kubernetes: KubernetesConfig = Field(default_factory=KubernetesConfig)
    gitops: GitOpsConfig = Field(default_factory=GitOpsConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    runbooks_dir: str = "runbooks"
    output_dir: str = "out"
    audit_path: str = "out/audit.jsonl"


_ENV_OVERRIDES = {
    "AIOPS_LLM_BASE_URL": ("llm", "base_url"),
    "AIOPS_LLM_MODEL": ("llm", "model"),
    "AIOPS_CLOUD_ENABLED": ("cloud", "enabled"),
    "AIOPS_PROMETHEUS_URL": ("prometheus", "url"),
    "AIOPS_LOKI_URL": ("loki", "url"),
    "AIOPS_AUTONOMY_LEVEL": ("policy", "autonomy_level"),
    "AIOPS_GITOPS_DRY_RUN": ("gitops", "dry_run"),
}


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    data: dict = {}
    path = path or os.environ.get("AIOPS_CONFIG")
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text()) or {}
    for env, (section, key) in _ENV_OVERRIDES.items():
        if env in os.environ:
            data.setdefault(section, {})[key] = os.environ[env]
    return Settings.model_validate(data)
