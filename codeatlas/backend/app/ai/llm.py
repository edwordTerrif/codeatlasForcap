"""LLM 연결부. 외부 라이브러리 없이 표준 라이브러리(urllib)로 HTTP를 호출한다.

지원:
  none       LLM 없이 동작 (요약은 docstring/구조 기반, 질의응답은 검색 결과만)
  openai     OpenAI 호환 /chat/completions (OpenAI, Groq, LM Studio 등)
  ollama     내 컴퓨터의 Ollama (OpenAI 호환 엔드포인트, 무료)
  anthropic  Claude Messages API
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from ..config import Settings


class LLMError(Exception):
    """LLM 호출 실패."""


class BaseLLM:
    provider = "none"
    model = ""
    available = False
    warning = ""

    @property
    def tag(self) -> str:
        return f"{self.provider}:{self.model}" if self.available else "none"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        raise LLMError("LLM이 설정되지 않았습니다 (.env의 LLM_PROVIDER 확인)")

    def describe(self) -> dict:
        return {"provider": self.provider, "model": self.model, "available": self.available, "warning": self.warning}


class NoLLM(BaseLLM):
    def __init__(self, warning: str = ""):
        self.warning = warning


def _post_json(url: str, body: dict, headers: dict, timeout: int, retries: int = 2) -> dict:
    data = json.dumps(body).encode("utf-8")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (408, 409, 429, 500, 502, 503, 504, 529) and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise LLMError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(f"연결 실패: {e}") from e
        except json.JSONDecodeError as e:
            raise LLMError("응답이 JSON이 아닙니다") from e
    raise LLMError("알 수 없는 오류")


class OpenAICompatibleLLM(BaseLLM):
    available = True

    def __init__(self, provider: str, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.provider, self.base_url, self.api_key, self.model, self.timeout = provider, base_url, api_key, model, timeout

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        # 모델마다 max_tokens 파라미터 이름이 달라서 넣지 않는다 (프롬프트로 길이를 제한)
        data = _post_json(self.base_url.rstrip("/") + "/chat/completions", body, headers, self.timeout)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"응답 형식을 해석할 수 없습니다: {str(data)[:200]}") from e
        return text.strip()


class AnthropicLLM(BaseLLM):
    provider = "anthropic"
    available = True

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url, self.api_key, self.model, self.timeout = base_url, api_key, model, timeout

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = _post_json(self.base_url.rstrip("/") + "/v1/messages", body, headers, self.timeout)
        blocks = data.get("content") if isinstance(data, dict) else None
        text = "".join(b.get("text", "") for b in (blocks or []) if isinstance(b, dict) and b.get("type") == "text")
        if not text:
            raise LLMError(f"응답에 텍스트가 없습니다: {str(data)[:200]}")
        return text.strip()


def get_llm(settings: Settings) -> BaseLLM:
    p = settings.llm_provider
    if p in ("openai", "ollama"):
        if not settings.llm_model:
            return NoLLM("LLM_MODEL이 비어 있어 AI 기능을 끈 상태로 실행합니다.")
        if p == "openai" and not settings.llm_api_key:
            return NoLLM("OPENAI_API_KEY(또는 LLM_API_KEY)가 없어 AI 기능을 끈 상태로 실행합니다.")
        return OpenAICompatibleLLM(p, settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout)
    if p == "anthropic":
        if not settings.llm_api_key:
            return NoLLM("ANTHROPIC_API_KEY(또는 LLM_API_KEY)가 없어 AI 기능을 끈 상태로 실행합니다.")
        return AnthropicLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout)
    return NoLLM()
