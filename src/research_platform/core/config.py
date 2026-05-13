from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    data_dir: Path = Path("./data")
    database_url: str = "postgresql+psycopg://postgres:change_me@localhost:5432/company_intelligence"
    pgvector_enabled: bool = True
    default_framework: str = "IVF_PRE_SCREEN"
    default_exchange: str = "LSE"
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:14b"
    llm_timeout_seconds: int = 120
    ollama_base_url: str = "http://localhost:11434"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_http_referer: str | None = None
    openrouter_app_title: str = "company-intelligence-platform"
    framework_runner_config_path: Path = Path("./config/framework_runner.yaml")
    ivf_pre_screen_temperature: float = 0.1
    ivf_pre_screen_max_repair_attempts: int = 1
    nsm_config_path: Path = Path("./config/nsm.yaml")
    nsm_base_url: str = "https://data.fca.org.uk/#/nsm/nationalstoragemechanism"
    nsm_download_dir: Path = Path("./data/downloads/nsm")
    nsm_artifact_dir: Path = Path("./data/artifacts/nsm")
    nsm_cookie_accept_selector: str = "button:has-text('Yes, I agree')"
    nsm_cookie_reject_selector: str = ""
    nsm_terms_accept_selector: str = "#acceptDisc"
    nsm_terms_container_selector: str = ".show_disclaimer"
    nsm_ready_selector: str = "#orgName"
    nsm_ready_timeout_ms: int = 20000
    nsm_shell_settle_ms: int = 750
    nsm_cookie_settle_ms: int = 500
    nsm_terms_settle_ms: int = 500
    nsm_loading_overlay_selector: str = ".ngx-spinner-overlay"
    nsm_loading_timeout_ms: int = 30000
    nsm_results_container_selector: str = ".nsmtableData"
    nsm_results_table_selector: str = "table#table"
    nsm_error_selector: str = ".nsmError"
    nsm_results_timeout_ms: int = 30000
    nsm_search_input_selector: str = "#orgName"
    nsm_category_dropdown_selector: str = "#headlineCategory"
    nsm_submit_selector: str = "#Nsm_search"
    nsm_result_row_selector: str = "tbody#tableBody tr[id^='tablerow-']"
    nsm_result_title_selector: str = "td[data-before='Description'] .head_link"
    nsm_result_date_selector: str = "td[data-before='Filing Date/Time'] summary"
    nsm_result_org_name_selector: str = "td[data-before='Disclosing Organisation Name'] summary"
    nsm_result_category_selector: str = "td[data-before='Category'] summary"
    nsm_result_link_selector: str = "td[data-before='Description'] a[href]"
    nsm_download_dialog_selector: str = "#dialog4"
    nsm_download_button_selector: str = "#focus4"
    browser_channel: str | None = Field(
        default=None,
        description="Optional Playwright browser channel such as chrome or msedge.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    _apply_framework_runner_yaml_defaults(settings)
    _apply_nsm_yaml_defaults(settings)
    return settings


def _apply_framework_runner_yaml_defaults(settings: Settings) -> None:
    config_path = settings.framework_runner_config_path
    if not config_path.exists():
        return

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return

    ivf_config = payload.get("ivf_pre_screen")
    if not isinstance(ivf_config, dict):
        return

    _set_if_default(settings, "ivf_pre_screen_temperature", ivf_config.get("temperature"))
    _set_if_default(
        settings,
        "ivf_pre_screen_max_repair_attempts",
        ivf_config.get("max_repair_attempts"),
    )


def _apply_nsm_yaml_defaults(settings: Settings) -> None:
    config_path = settings.nsm_config_path
    if not config_path.exists():
        return

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return

    for key, value in payload.items():
        _set_if_default(settings, key, value)


def _set_if_default(settings: Settings, key: str, value: object) -> None:
    if value is None or key not in Settings.model_fields:
        return

    field_info = Settings.model_fields[key]
    default_value = field_info.default
    current_value = getattr(settings, key)
    if current_value == default_value:
        coerced_value = TypeAdapter(field_info.annotation).validate_python(value)
        setattr(settings, key, coerced_value)
