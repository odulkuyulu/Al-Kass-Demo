"""
Near real-time translation pipeline.

This module implements the streaming path:
  Audio Input → Speech-to-Text (streaming) → Translation → Caption Output

BROADCAST DESIGN NOTES:
- Uses Azure Speech SDK continuous recognition for streaming STT.
- Partial (interim) results are translated immediately, not held for sentence
  completion. This introduces some caption "flicker" but keeps latency low.
- Final (confirmed) results replace partials for accuracy.
- The caption_callback receives incremental updates suitable for broadcast
  overlay systems.
- Latency is measured and logged per segment.

TRADE-OFF: SPEED vs ACCURACY
- Partials are fast but may contain recognition errors that get corrected
  in finals. The pipeline emits both, clearly labelled, so the downstream
  caption renderer can decide how to handle them (e.g., show partials in
  grey, finals in white).
"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Callable, Optional

from .config import PipelineConfig, TranslationDirection, AuthMode
from .glossary import DomainGlossary, load_glossary
from .observability import PipelineLogger, SegmentTrace, StageMetrics
from .translation_service import TranslationService


class CaptionType(Enum):
    PARTIAL = "partial"     # Interim, may change
    FINAL = "final"         # Confirmed, stable


@dataclass
class CaptionEvent:
    """A single caption update emitted by the pipeline."""
    caption_type: CaptionType
    source_text: str
    translated_text: str
    source_language: str
    target_language: str
    timestamp_ms: float         # Wall-clock time when emitted
    latency_ms: float           # End-to-end latency for this segment
    segment_id: str = ""


class RealTimeTranslationPipeline:
    """
    Orchestrates near real-time speech-to-text → translation → caption output.

    Usage:
        pipeline = RealTimeTranslationPipeline(config)
        pipeline.start(audio_source, caption_callback)
        # ... later ...
        pipeline.stop()

    The audio_source can be:
      - None: uses default microphone
      - str: path to an audio file (for testing)
      - An Azure AudioConfig object (for custom streams)
    """

    def __init__(self, config: PipelineConfig):
        self._config = config
        self._glossary = load_glossary(config.glossary_path)
        self._translator = TranslationService(
            config=config.translator,
            glossary=self._glossary,
            timeout_s=config.latency.translation_timeout_s,
        )
        self._logger = PipelineLogger(
            name="alkass.realtime",
            level=config.log_level,
        )
        self._recogniser = None
        self._running = False
        self._caption_callback: Optional[Callable] = None

        # Determine source/target languages from direction
        if config.direction == TranslationDirection.AR_TO_EN:
            self._source_locale = config.speech.arabic_locale
            self._source_lang = "ar"
            self._target_lang = "en"
        else:
            self._source_locale = config.speech.english_locale
            self._source_lang = "en"
            self._target_lang = "ar"

    def start(self, audio_source=None, caption_callback: Callable = None):
        """
        Start the real-time translation pipeline.

        Args:
            audio_source: None for microphone, str for file path,
                          or azure.cognitiveservices.speech.audio.AudioConfig.
            caption_callback: Called with each CaptionEvent.
                              Signature: callback(event: CaptionEvent) -> None
        """
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise ImportError(
                "azure-cognitiveservices-speech is required for real-time mode. "
                "Install with: pip install azure-cognitiveservices-speech"
            )

        self._caption_callback = caption_callback or self._default_caption_handler

        # Configure Speech SDK — key auth or Entra ID token auth
        if self._config.speech.auth_mode == AuthMode.KEY:
            speech_config = speechsdk.SpeechConfig(
                subscription=self._config.speech.subscription_key,
                region=self._config.speech.region,
            )
        else:
            # Entra ID: obtain a token and use the custom endpoint
            # Speech SDK requires setting auth_token AFTER creating config
            # with endpoint, not in the constructor.
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
            import os
            client_id = os.environ.get("AZURE_CLIENT_ID", "")
            if client_id:
                credential = ManagedIdentityCredential(client_id=client_id)
            else:
                credential = DefaultAzureCredential()
            token = credential.get_token(
                "https://cognitiveservices.azure.com/.default"
            )
            endpoint = self._config.speech.endpoint
            region = self._config.speech.region
            resource_id = self._config.speech.resource_id
            self._logger.log_info(
                f"Using Entra ID token auth, endpoint={endpoint}"
            )
            # For Entra ID auth with Speech SDK, we need to prefix the token
            # with the resource ID so the service knows which resource to auth against.
            aad_token = f"aad#{resource_id}#{token.token}"
            speech_config = speechsdk.SpeechConfig(
                auth_token=aad_token,
                region=region,
            )
        speech_config.speech_recognition_language = self._source_locale
        speech_config.set_profanity(
            speechsdk.ProfanityOption.Raw
            if self._config.speech.profanity_option == "raw"
            else speechsdk.ProfanityOption.Masked
        )
        # Request detailed output for better timing data
        speech_config.output_format = speechsdk.OutputFormat.Detailed

        # Configure audio input
        if audio_source is None:
            audio_config = speechsdk.audio.AudioConfig(
                use_default_microphone=True
            )
        elif isinstance(audio_source, str):
            audio_config = speechsdk.audio.AudioConfig(
                filename=audio_source
            )
        else:
            audio_config = audio_source

        self._recogniser = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Wire up event handlers
        self._recogniser.recognizing.connect(self._on_recognizing)
        self._recogniser.recognized.connect(self._on_recognized)
        self._recogniser.canceled.connect(self._on_canceled)
        self._recogniser.session_stopped.connect(self._on_session_stopped)

        self._running = True
        self._logger.log_info(
            f"Starting real-time pipeline: {self._source_lang} → {self._target_lang}"
        )
        self._recogniser.start_continuous_recognition()

    def stop(self):
        """Stop the real-time pipeline."""
        if self._recogniser and self._running:
            self._recogniser.stop_continuous_recognition()
            self._running = False
            self._logger.log_info("Real-time pipeline stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    def _on_recognizing(self, evt):
        """
        Handle partial (interim) recognition results.

        BROADCAST NOTE: These fire frequently and represent the STT engine's
        current best guess. They WILL change. We translate them anyway for
        low-latency caption display, but mark them as PARTIAL.
        """
        if not evt.result.text:
            return

        recognition_time = time.time()
        segment_id = str(uuid.uuid4())[:8]
        source_text = evt.result.text

        # Translate the partial result
        translated, translate_metrics = self._translator.translate(
            source_text, self._config.direction
        )

        emit_time = time.time()

        # Build trace for observability
        trace = SegmentTrace(
            segment_id=segment_id,
            source_language=self._source_lang,
            target_language=self._target_lang,
            source_text=source_text,
            translated_text=translated,
        )
        stt_metrics = StageMetrics(
            stage_name="stt_partial",
            start_time=recognition_time,
            end_time=recognition_time,
            input_length=0,
            output_length=len(source_text),
        )
        trace.add_stage(stt_metrics)
        trace.add_stage(translate_metrics)

        # Emit caption
        event = CaptionEvent(
            caption_type=CaptionType.PARTIAL,
            source_text=source_text,
            translated_text=translated,
            source_language=self._source_lang,
            target_language=self._target_lang,
            timestamp_ms=emit_time * 1000,
            latency_ms=trace.total_latency_ms,
            segment_id=segment_id,
        )

        if self._config.log_latency_per_segment:
            self._logger.log_debug(
                f"PARTIAL [{segment_id}] latency={trace.total_latency_ms:.0f}ms"
            )

        if self._caption_callback:
            self._caption_callback(event)

    def _on_recognized(self, evt):
        """
        Handle final (confirmed) recognition results.

        BROADCAST NOTE: Finals replace any preceding partials for the same
        utterance. These are more accurate and should be used for the
        definitive caption display.
        """
        if not evt.result.text:
            return

        recognition_time = time.time()
        segment_id = str(uuid.uuid4())[:8]
        source_text = evt.result.text

        # Translate the final result
        translated, translate_metrics = self._translator.translate(
            source_text, self._config.direction
        )

        emit_time = time.time()

        # Build trace
        trace = SegmentTrace(
            segment_id=segment_id,
            source_language=self._source_lang,
            target_language=self._target_lang,
            source_text=source_text,
            translated_text=translated,
        )
        stt_metrics = StageMetrics(
            stage_name="stt_final",
            start_time=recognition_time,
            end_time=recognition_time,
            output_length=len(source_text),
        )
        trace.add_stage(stt_metrics)
        trace.add_stage(translate_metrics)

        event = CaptionEvent(
            caption_type=CaptionType.FINAL,
            source_text=source_text,
            translated_text=translated,
            source_language=self._source_lang,
            target_language=self._target_lang,
            timestamp_ms=emit_time * 1000,
            latency_ms=trace.total_latency_ms,
            segment_id=segment_id,
        )

        self._logger.log_segment(
            trace, self._config.latency.max_acceptable_latency_ms
        )

        if self._caption_callback:
            self._caption_callback(event)

    def _on_canceled(self, evt):
        """Handle recognition cancellation (errors, end of stream)."""
        try:
            import azure.cognitiveservices.speech as speechsdk
            cancellation = evt.result.cancellation_details
            self._logger.log_info(
                f"Recognition cancelled: reason={cancellation.reason}, "
                f"error_details={cancellation.error_details}"
            )
        except Exception as e:
            self._logger.log_error("stt", f"Cancellation handler error: {e}")

    def _on_session_stopped(self, evt):
        """Handle session termination."""
        self._running = False
        self._logger.log_info("Speech recognition session ended.")

    def _default_caption_handler(self, event: CaptionEvent):
        """Default handler: print captions to stdout."""
        prefix = ">>>" if event.caption_type == CaptionType.FINAL else "..."
        print(
            f"{prefix} [{event.latency_ms:.0f}ms] "
            f"{event.translated_text}"
        )
