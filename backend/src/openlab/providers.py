"""One deliberately small OpenAI-compatible provider boundary.

OpenRouter, OpenAI, Ollama, LM Studio, vLLM and many hosted providers expose
``/v1/chat/completions``. Keeping the HTTP contract here avoids vendor SDKs and
keeps keys out of the browser.
"""

import base64
import json
from collections.abc import Sequence
from typing import NoReturn, Protocol, TypedDict, cast
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken


class Candidate(TypedDict):
    name: str
    description: str | None
    quantity: float
    category: str
    identity_confidence: str
    observations: list[str]


class ProviderError(RuntimeError):
    pass


class AIProvider(Protocol):
    name: str

    def generate_structured(
        self, prompt: str, schema_name: str, schema: dict[str, object] | None = None
    ) -> dict[str, object]: ...

    def analyze_image(self, content: bytes, content_type: str) -> Sequence[Candidate]: ...

    def transcribe(self, content: bytes, content_type: str) -> str: ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


def is_local_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"}


def encrypt_secret(value: str, installation_key: str | None) -> str:
    if not installation_key:
        raise ProviderError("OPENLAB_ENCRYPTION_KEY is required before storing an API key")
    try:
        return Fernet(installation_key.encode()).encrypt(value.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise ProviderError("OPENLAB_ENCRYPTION_KEY must be a Fernet key") from exc


def decrypt_secret(value: str | None, installation_key: str | None) -> str | None:
    if value is None:
        return None
    if not installation_key:
        raise ProviderError("The installation encryption key is unavailable")
    try:
        return Fernet(installation_key.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ProviderError("The stored provider key cannot be decrypted") from exc


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(self, *, base_url: str, model: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _completion(
        self,
        messages: list[dict[str, object]],
        *,
        json_mode: bool = True,
        max_tokens: int = 2048,
        retry_on_truncation: bool = True,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "openlab_output",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        elif json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions", headers=self._headers, json=body, timeout=60
            )
            if response.status_code == 400 and response_schema is not None:
                return self._completion(
                    messages,
                    json_mode=True,
                    max_tokens=max_tokens,
                    retry_on_truncation=retry_on_truncation,
                )
            if response.status_code == 400 and json_mode:
                return self._completion(
                    messages,
                    json_mode=False,
                    max_tokens=max_tokens,
                    retry_on_truncation=retry_on_truncation,
                )
            response.raise_for_status()
            payload = response.json()
            choice = payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            message = choice["message"]
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type", "text") == "text"
                )
            if not isinstance(content, str) or not content.strip():
                if finish_reason == "length" and retry_on_truncation:
                    return self._completion(
                        messages,
                        json_mode=False,
                        max_tokens=min(max_tokens * 2, 4096),
                        retry_on_truncation=False,
                    )
                raise ProviderError(
                    f"Provider returned no text content (finish_reason={finish_reason or 'unknown'})"
                )
            if finish_reason == "length" and retry_on_truncation:
                return self._completion(
                    messages,
                    json_mode=False,
                    max_tokens=min(max_tokens * 2, 4096),
                    retry_on_truncation=False,
                )
            return content
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"Provider request failed: {exc}") from exc

    def generate_structured(
        self, prompt: str, schema_name: str, schema: dict[str, object] | None = None
    ) -> dict[str, object]:
        content = self._completion(
            [
                {"role": "system", "content": f"Return only JSON matching {schema_name}."},
                {"role": "user", "content": prompt},
            ],
            response_schema=schema,
        )
        return parse_json_object(content)

    def analyze_image(self, content: bytes, content_type: str) -> Sequence[Candidate]:
        result = self._inbox_completion("", [(content, content_type)])
        candidates = result.get("candidates", [])
        if not isinstance(candidates, list):
            raise ProviderError("Provider returned invalid candidates")
        return cast(Sequence[Candidate], candidates)

    def transcribe(self, content: bytes, content_type: str) -> str:
        try:
            response = httpx.post(
                f"{self.base_url}/audio/transcriptions",
                headers={k: v for k, v in self._headers.items() if k != "Content-Type"},
                data={"model": self.model},
                files={"file": ("voice-input", content, content_type)},
                timeout=90,
            )
            response.raise_for_status()
            text = response.json().get("text")
            if not isinstance(text, str):
                raise ProviderError("Provider did not return a transcription")
            return text
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Voice transcription failed: {exc}") from exc

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=self._headers,
                json={"model": self.model, "input": list(texts)},
                timeout=60,
            )
            response.raise_for_status()
            return [row["embedding"] for row in response.json()["data"]]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Embedding request failed: {exc}") from exc

    def extract_inbox(
        self,
        text: str,
        images: Sequence[tuple[bytes, str]] = (),
        repair_error: str | None = None,
    ) -> dict[str, object]:
        return self._inbox_completion(text, images, repair_error=repair_error)

    def _inbox_completion(
        self,
        text: str,
        images: Sequence[tuple[bytes, str]],
        *,
        repair_error: str | None = None,
    ) -> dict[str, object]:
        prompt = (
            "Classify this capture into electronics inventory candidates. Return JSON only with "
            "exactly this shape: "
            '{"candidates":[{"name":string,"description":string|null,"quantity":number,'
            '"category":"module|ic|board|sensor|passive|connector|power|tool|other|uncategorized",'
            '"identity_confidence":"high|medium|low|unresolved","observations":[string]}]}. '
            "Use the shortest stable identity for name; category carries the physical form, so an "
            "assembled MCP23017 module is named MCP23017 with category module. Never copy a full "
            "marketplace title into name. Put a short functional sentence in description. Prefer a "
            "selected listing option or visible marking over a generic multi-variant title. Never "
            "combine conflicting part numbers; record conflicts in observations. Do not return raw "
            "source text. Do not invent manufacturer, part number, voltage, pinout, interface, or "
            "other technical facts. Use null or [] when unsupported. Never explain your answer. "
            "Include at most 25 candidates, keep names under 160 characters, descriptions under 600 "
            "characters, and observations to at most 8 short entries. "
            f"Capture text: {text or '[no text]'}"
        )
        if repair_error:
            prompt = (
                f"{prompt}\nA previous response failed schema validation: {repair_error}. "
                "Return a corrected object with no extra fields."
            )
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for raw, mime in images:
            encoded = base64.b64encode(raw).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
            )
        reply = self._completion(
            [
                {"role": "system", "content": "You extract inventory conservatively."},
                {"role": "user", "content": content},
            ],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["candidates"],
                "properties": {
                    "candidates": {
                        "type": "array",
                        "maxItems": 25,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "name",
                                "description",
                                "quantity",
                                "category",
                                "identity_confidence",
                                "observations",
                            ],
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": ["string", "null"]},
                                "quantity": {"type": "number", "exclusiveMinimum": 0},
                                "category": {
                                    "type": "string",
                                    "enum": [
                                        "module",
                                        "ic",
                                        "board",
                                        "sensor",
                                        "passive",
                                        "connector",
                                        "power",
                                        "tool",
                                        "other",
                                        "uncategorized",
                                    ],
                                },
                                "identity_confidence": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low", "unresolved"],
                                },
                                "observations": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    }
                },
            },
        )
        return parse_json_object(reply)

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.base_url}/models", headers=self._headers, timeout=20)
            response.raise_for_status()
            return sorted(
                str(item["id"]) for item in response.json().get("data", []) if item.get("id")
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Could not list provider models: {exc}") from exc


def parse_json_object(content: str) -> dict[str, object]:
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        # Reasoning-capable OpenRouter models sometimes put a short explanation
        # before or after the JSON despite the response-format request. Decode
        # the first complete object rather than failing the whole Inbox job.
        decoder = json.JSONDecoder()
        for offset, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[offset:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ProviderError("Provider returned invalid JSON") from None
    if not isinstance(value, dict):
        raise ProviderError("Provider returned a JSON value instead of an object")
    return value


class DisabledProvider:
    name = "disabled"

    def _disabled(self) -> NoReturn:
        raise ProviderError("AI is disabled for this lab")

    def generate_structured(
        self, prompt: str, schema_name: str, schema: dict[str, object] | None = None
    ) -> dict[str, object]:
        _ = schema
        self._disabled()

    def analyze_image(self, content: bytes, content_type: str) -> Sequence[Candidate]:
        self._disabled()

    def transcribe(self, content: bytes, content_type: str) -> str:
        self._disabled()

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self._disabled()
