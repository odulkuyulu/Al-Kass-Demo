"""
Translation service: wraps Azure Translator API with glossary post-processing.

Shared between the near real-time and offline paths.
Supports both API key and Entra ID (token-based) authentication.
"""

import time
import uuid
from typing import List, Optional

import requests

from .config import AuthMode, AzureTranslatorConfig, TranslationDirection
from .glossary import DomainGlossary
from .observability import StageMetrics


def _get_translator_token(config: AzureTranslatorConfig) -> str:
    """Obtain a bearer token for Azure Translator using azure-identity."""
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token


class TranslationService:
    """
    Translates text between Arabic and English via Azure Translator.

    BROADCAST CONSIDERATION:
    - Each call is stateless; the service does not maintain conversation context.
    - For near real-time, partial sentences may be translated. This is intentional:
      we prioritise low latency over waiting for complete sentences.
    - Glossary corrections are applied post-translation to fix domain terms.

    AUTHENTICATION:
    - If config.subscription_key is set, uses API key auth.
    - Otherwise, uses Entra ID token (DefaultAzureCredential).
    """

    # Azure Translator language codes
    _LANG_MAP = {
        TranslationDirection.AR_TO_EN: ("ar", "en"),
        TranslationDirection.EN_TO_AR: ("en", "ar"),
    }

    def __init__(
        self,
        config: AzureTranslatorConfig,
        glossary: Optional[DomainGlossary] = None,
        timeout_s: float = 3.0,
    ):
        self._config = config
        self._glossary = glossary or DomainGlossary()
        self._timeout = timeout_s
        self._session = requests.Session()
        self._cached_token: Optional[str] = None

    def _build_headers(self) -> dict:
        """Build request headers with either API key or Entra ID token."""
        headers = {
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        if self._config.auth_mode == AuthMode.KEY:
            headers["Ocp-Apim-Subscription-Key"] = self._config.subscription_key
            headers["Ocp-Apim-Subscription-Region"] = self._config.region
        else:
            # Entra ID token auth
            token = _get_translator_token(self._config)
            headers["Authorization"] = f"Bearer {token}"
            headers["Ocp-Apim-Subscription-Region"] = self._config.region
        return headers

    def translate(
        self,
        text: str,
        direction: TranslationDirection,
    ) -> tuple:
        """
        Translate text and return (translated_text, stage_metrics).

        Returns:
            Tuple of (translated_text: str, metrics: StageMetrics)
        """
        metrics = StageMetrics(stage_name="translation")
        metrics.start_time = time.time()
        metrics.input_length = len(text)

        if not text.strip():
            metrics.end_time = time.time()
            return "", metrics

        source_lang, target_lang = self._LANG_MAP[direction]

        try:
            result = self._call_translator_api(text, source_lang, target_lang)
            # Apply domain glossary corrections
            result = self._glossary.apply(result, direction.value)
            metrics.output_length = len(result)
            metrics.end_time = time.time()
            return result, metrics

        except Exception as e:
            metrics.error = str(e)
            metrics.end_time = time.time()
            return f"[TRANSLATION ERROR: {e}]", metrics

    def translate_batch(
        self,
        texts: List[str],
        direction: TranslationDirection,
    ) -> tuple:
        """
        Translate multiple texts in a single API call (offline path).
        Azure Translator supports up to 100 elements per request.

        Returns:
            Tuple of (translated_texts: List[str], metrics: StageMetrics)
        """
        metrics = StageMetrics(stage_name="translation_batch")
        metrics.start_time = time.time()
        metrics.input_length = sum(len(t) for t in texts)

        if not texts:
            metrics.end_time = time.time()
            return [], metrics

        source_lang, target_lang = self._LANG_MAP[direction]

        try:
            results = self._call_translator_api_batch(texts, source_lang, target_lang)
            # Apply glossary to each result
            results = [
                self._glossary.apply(r, direction.value) for r in results
            ]
            metrics.output_length = sum(len(r) for r in results)
            metrics.end_time = time.time()
            return results, metrics

        except Exception as e:
            metrics.error = str(e)
            metrics.end_time = time.time()
            return [f"[TRANSLATION ERROR: {e}]"] * len(texts), metrics

    def _get_base_url(self) -> str:
        """Return the base URL depending on auth mode."""
        if self._config.auth_mode == AuthMode.ENTRA and self._config.custom_endpoint:
            # Custom-domain Translator endpoints use a different path prefix
            return self._config.custom_endpoint.rstrip("/") + "/translator/text/v3.0"
        return self._config.endpoint.rstrip("/")

    def _call_translator_api(self, text: str, source: str, target: str) -> str:
        """Single-text Azure Translator API call."""
        url = f"{self._get_base_url()}/translate"
        params = {
            "api-version": "3.0",
            "from": source,
            "to": target,
        }
        headers = self._build_headers()
        body = [{"text": text}]

        response = self._session.post(
            url, params=params, headers=headers, json=body, timeout=self._timeout
        )
        response.raise_for_status()
        data = response.json()
        return data[0]["translations"][0]["text"]

    def _call_translator_api_batch(
        self, texts: List[str], source: str, target: str
    ) -> List[str]:
        """Batch Azure Translator API call (up to 100 items)."""
        url = f"{self._get_base_url()}/translate"
        params = {
            "api-version": "3.0",
            "from": source,
            "to": target,
        }
        headers = self._build_headers()
        # Azure Translator batch limit: 100 elements, 10,000 chars total
        body = [{"text": t} for t in texts]

        response = self._session.post(
            url, params=params, headers=headers, json=body, timeout=self._timeout
        )
        response.raise_for_status()
        data = response.json()
        return [item["translations"][0]["text"] for item in data]
