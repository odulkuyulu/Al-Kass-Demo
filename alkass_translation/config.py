"""
Configuration management for the Alkass Translation Platform.

Supports three environments: demo, poc, production.
All latency/accuracy trade-offs are surfaced as explicit configuration
so operators can tune behaviour per deployment context.

AUTHENTICATION:
  Supports two modes:
  1. API Key (set AZURE_SPEECH_KEY / AZURE_TRANSLATOR_KEY env vars)
  2. Entra ID / Token-based (default when keys are not set)
     Uses DefaultAzureCredential from azure-identity, which tries:
       - InteractiveBrowserCredential (opens browser)
       - AzureCliCredential
       - VisualStudioCodeCredential
       - ManagedIdentityCredential
     Requires role assignments: "Cognitive Services Speech User" and
     "Cognitive Services User" on the respective resources.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Environment(Enum):
    DEMO = "demo"
    POC = "poc"
    PRODUCTION = "production"


class AuthMode(Enum):
    KEY = "key"         # API key authentication
    ENTRA = "entra"     # Microsoft Entra ID (token-based)


class TranslationDirection(Enum):
    AR_TO_EN = "ar-to-en"
    EN_TO_AR = "en-to-ar"


@dataclass
class AzureSpeechConfig:
    """Azure Cognitive Services Speech SDK configuration."""
    subscription_key: str = ""
    region: str = "qatarcentral"
    # Resource ID for Entra auth (from Azure Portal → resource → Properties)
    resource_id: str = ""
    # Custom endpoint (required for Entra auth)
    endpoint: str = ""
    # Speech recognition language codes
    arabic_locale: str = "ar-QA"       # Qatar Arabic
    english_locale: str = "en-US"
    # Profanity filter: masked | removed | raw
    profanity_option: str = "raw"      # Broadcast: keep original

    def __post_init__(self):
        if not self.subscription_key:
            self.subscription_key = os.environ.get("AZURE_SPEECH_KEY", "")
        if not self.region:
            self.region = os.environ.get("AZURE_SPEECH_REGION", "qatarcentral")
        if not self.resource_id:
            self.resource_id = os.environ.get("AZURE_SPEECH_RESOURCE_ID", "")
        if not self.endpoint:
            self.endpoint = os.environ.get(
                "AZURE_SPEECH_ENDPOINT",
                f"https://{self.region}.api.cognitive.microsoft.com"
            )

    @property
    def auth_mode(self) -> AuthMode:
        return AuthMode.KEY if self.subscription_key else AuthMode.ENTRA


@dataclass
class AzureTranslatorConfig:
    """Azure Translator API configuration."""
    subscription_key: str = ""
    region: str = ""
    # Global endpoint (used with key auth)
    endpoint: str = "https://api.cognitive.microsofttranslator.com"
    # Custom domain endpoint for Entra ID auth
    # (from Azure Portal → Translator resource → Keys and Endpoint → Document Translation endpoint)
    custom_endpoint: str = ""

    def __post_init__(self):
        if not self.subscription_key:
            self.subscription_key = os.environ.get("AZURE_TRANSLATOR_KEY", "")
        if not self.region:
            self.region = os.environ.get("AZURE_TRANSLATOR_REGION", "westeurope")
        if not self.custom_endpoint:
            self.custom_endpoint = os.environ.get("AZURE_TRANSLATOR_ENDPOINT", "")

    @property
    def auth_mode(self) -> AuthMode:
        return AuthMode.KEY if self.subscription_key else AuthMode.ENTRA


@dataclass
class LatencyConfig:
    """
    Explicit latency parameters.

    BROADCAST TRADE-OFF:
    - Lower caption_emit_interval_ms → faster caption updates but more flicker.
    - Higher values → smoother captions but more perceived delay.
    - stabilisation_window_ms controls how long we wait for a recognition
      to stabilise before emitting (reduces "caption jitter").
    """
    # How often to push caption updates to the output (milliseconds)
    caption_emit_interval_ms: int = 500
    # How long to wait for partial recognition to stabilise
    stabilisation_window_ms: int = 300
    # Maximum acceptable end-to-end latency before logging a warning
    max_acceptable_latency_ms: int = 5000
    # Translation API timeout
    translation_timeout_s: float = 3.0


@dataclass
class TTSConfig:
    """Text-to-speech configuration for optional dubbed audio output."""
    enabled: bool = False
    arabic_voice: str = "ar-QA-AmalNeural"
    english_voice: str = "en-US-JennyNeural"
    output_format: str = "audio-16khz-128kbitrate-mono-mp3"


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration, aggregating all sub-configs."""
    environment: Environment = Environment.DEMO
    speech: AzureSpeechConfig = field(default_factory=AzureSpeechConfig)
    translator: AzureTranslatorConfig = field(default_factory=AzureTranslatorConfig)
    latency: LatencyConfig = field(default_factory=LatencyConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    # Default translation direction
    direction: TranslationDirection = TranslationDirection.AR_TO_EN
    # Domain glossary file path (CSV: source_term,target_term)
    glossary_path: Optional[str] = None
    # Logging
    log_level: str = "INFO"
    log_latency_per_segment: bool = True

    @classmethod
    def for_environment(cls, env: str) -> "PipelineConfig":
        """Factory: build config tuned for a specific environment."""
        environment = Environment(env)
        if environment == Environment.DEMO:
            return cls(
                environment=environment,
                latency=LatencyConfig(
                    caption_emit_interval_ms=400,
                    max_acceptable_latency_ms=8000,
                ),
                log_level="DEBUG",
            )
        elif environment == Environment.POC:
            return cls(
                environment=environment,
                latency=LatencyConfig(
                    caption_emit_interval_ms=500,
                    max_acceptable_latency_ms=5000,
                ),
                log_level="INFO",
            )
        else:  # PRODUCTION
            return cls(
                environment=environment,
                latency=LatencyConfig(
                    caption_emit_interval_ms=300,
                    stabilisation_window_ms=200,
                    max_acceptable_latency_ms=3000,
                    translation_timeout_s=2.0,
                ),
                log_level="WARNING",
            )
