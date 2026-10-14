"""환경 설정.

프로젝트 최상위 폴더(또는 backend 폴더)의 `.env` 파일이나 환경 변수에서 값을 읽는다.
LLM 키가 없어도(LLM_PROVIDER=none) 분석·지도·검색·투어는 모두 동작한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """아주 단순한 .env 로더 (KEY=VALUE). 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT_DIR / ".env")
_load_dotenv(BACKEND_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# provider -> (기본 base_url, 기본 모델)
PROVIDER_DEFAULTS = {
    "none": ("", ""),
    "openai": ("https://api.openai.com/v1", ""),  # 모델은 .env의 LLM_MODEL로 지정
    "ollama": ("http://localhost:11434/v1", "qwen2.5-coder:7b"),
    "anthropic": ("https://api.anthropic.com", "claude-haiku-5-5"),
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    llm_timeout: int
    summary_max_entities: int
    summary_max_files: int
    summary_workers: int
    max_files: int
    max_file_bytes: int


def load_settings() -> Settings:
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower() or "none"
    if provider not in PROVIDER_DEFAULTS:
        provider = "none"
    default_url, default_model = PROVIDER_DEFAULTS[provider]
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key and provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key and provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    data_dir = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data"))).expanduser().resolve()
    return Settings(
        data_dir=data_dir,
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL", default_model).strip(),
        llm_api_key=api_key,
        llm_base_url=os.getenv("LLM_BASE_URL", default_url).strip(),
        llm_timeout=_int("LLM_TIMEOUT", 60),
        summary_max_entities=_int("SUMMARY_MAX_ENTITIES", 120),
        summary_max_files=_int("SUMMARY_MAX_FILES", 60),
        summary_workers=max(1, _int("SUMMARY_WORKERS", 4)),
        max_files=_int("MAX_FILES", 3000),
        max_file_bytes=_int("MAX_FILE_BYTES", 1_500_000),
    )


settings = load_settings()
