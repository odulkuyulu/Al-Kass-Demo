"""
Offline / post-production translation pipeline.

This module implements the batch path:
  Audio/Video File → Full Transcription → Translation → Subtitle Export
                                                       → Optional TTS Audio

BROADCAST DESIGN NOTES:
- The offline path prioritises accuracy over speed.
- Full audio is transcribed first, then segmented, then translated.
- This path produces broadcast-quality SRT/VTT subtitles.
- TTS (text-to-speech) dubbed audio is optional and secondary to captions.
- The pipeline is synchronous and processes files end-to-end.

LIMITATIONS (Phase 1):
- Single-file processing (no batch queue).
- No speaker diarisation (TODO: Phase 2).
- No automatic language detection (caller must specify direction).
"""

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import AuthMode, PipelineConfig, TranslationDirection
from .glossary import load_glossary
from .observability import PipelineLogger, SegmentTrace, StageMetrics
from .subtitles import SubtitleEntry, write_subtitles
from .translation_service import TranslationService


@dataclass
class TranscriptionSegment:
    """A single segment from speech-to-text with timing information."""
    text: str
    start_time_ms: float
    end_time_ms: float
    confidence: float = 0.0


@dataclass
class OfflineResult:
    """Complete result from offline pipeline processing."""
    source_file: str
    direction: str
    segments: List[TranscriptionSegment] = field(default_factory=list)
    translated_segments: List[str] = field(default_factory=list)
    subtitle_path_srt: Optional[str] = None
    subtitle_path_vtt: Optional[str] = None
    tts_audio_path: Optional[str] = None
    total_duration_ms: float = 0.0
    processing_time_s: float = 0.0
    stage_metrics: List[StageMetrics] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class OfflineTranslationPipeline:
    """
    End-to-end offline translation: audio → transcription → translation → subtitles.

    Usage:
        pipeline = OfflineTranslationPipeline(config)
        result = pipeline.process("match_commentary.wav")
        # result.subtitle_path_srt → "match_commentary.en.srt"
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
            name="alkass.offline",
            level=config.log_level,
        )

    def process(
        self,
        audio_path: str,
        output_dir: Optional[str] = None,
        subtitle_formats: List[str] = None,
    ) -> OfflineResult:
        """
        Process an audio file through the full offline pipeline.

        Args:
            audio_path: Path to the audio/video file.
            output_dir: Directory for output files. Defaults to same dir as input.
            subtitle_formats: List of formats to generate, e.g. ["srt", "vtt"].

        Returns:
            OfflineResult with all outputs and metrics.
        """
        if subtitle_formats is None:
            subtitle_formats = ["srt", "vtt"]

        start_time = time.time()
        result = OfflineResult(
            source_file=audio_path,
            direction=self._config.direction.value,
        )

        if not os.path.exists(audio_path):
            result.errors.append(f"File not found: {audio_path}")
            return result

        if output_dir is None:
            output_dir = str(Path(audio_path).parent)
        os.makedirs(output_dir, exist_ok=True)

        base_name = Path(audio_path).stem
        target_lang = "en" if self._config.direction == TranslationDirection.AR_TO_EN else "ar"

        # ── Stage 1: Transcription ──
        self._logger.log_info(f"Stage 1: Transcribing {audio_path}")
        segments, stt_metrics = self._transcribe(audio_path)
        result.segments = segments
        result.stage_metrics.append(stt_metrics)

        if stt_metrics.error:
            result.errors.append(f"Transcription error: {stt_metrics.error}")
            return result

        if not segments:
            result.errors.append("No speech detected in audio file.")
            result.processing_time_s = time.time() - start_time
            return result

        self._logger.log_info(f"Transcribed {len(segments)} segments.")

        # ── Stage 2: Translation ──
        self._logger.log_info("Stage 2: Translating segments")
        source_texts = [seg.text for seg in segments]

        # Use batch translation for efficiency
        translated_texts, trans_metrics = self._translator.translate_batch(
            source_texts, self._config.direction
        )
        result.translated_segments = translated_texts
        result.stage_metrics.append(trans_metrics)

        if trans_metrics.error:
            result.errors.append(f"Translation error: {trans_metrics.error}")

        # ── Stage 3: Subtitle Generation ──
        self._logger.log_info("Stage 3: Generating subtitles")
        sub_entries = []
        for i, (seg, trans) in enumerate(zip(segments, translated_texts), 1):
            sub_entries.append(SubtitleEntry(
                index=i,
                start_time_ms=seg.start_time_ms,
                end_time_ms=seg.end_time_ms,
                text=trans,
                original_text=seg.text,
            ))

        for fmt in subtitle_formats:
            out_path = os.path.join(output_dir, f"{base_name}.{target_lang}.{fmt}")
            write_subtitles(sub_entries, out_path, fmt=fmt)
            if fmt == "srt":
                result.subtitle_path_srt = out_path
            elif fmt == "vtt":
                result.subtitle_path_vtt = out_path
            self._logger.log_info(f"Wrote {fmt.upper()}: {out_path}")

        # ── Stage 4 (Optional): Text-to-Speech ──
        if self._config.tts.enabled:
            self._logger.log_info("Stage 4: Generating TTS audio")
            tts_path = os.path.join(output_dir, f"{base_name}.{target_lang}.mp3")
            tts_metrics = self._generate_tts(translated_texts, tts_path, target_lang)
            result.tts_audio_path = tts_path
            result.stage_metrics.append(tts_metrics)
            if tts_metrics.error:
                result.errors.append(f"TTS error: {tts_metrics.error}")

        result.processing_time_s = time.time() - start_time
        if segments:
            result.total_duration_ms = segments[-1].end_time_ms

        self._logger.log_info(
            f"Offline processing complete. "
            f"Duration: {result.total_duration_ms/1000:.1f}s audio, "
            f"processed in {result.processing_time_s:.1f}s. "
            f"Errors: {len(result.errors)}"
        )

        return result

    def _transcribe(self, audio_path: str) -> tuple:
        """
        Transcribe an audio file using Azure Speech SDK batch recognition.

        Returns:
            Tuple of (segments: List[TranscriptionSegment], metrics: StageMetrics)
        """
        metrics = StageMetrics(stage_name="transcription")
        metrics.start_time = time.time()

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            metrics.error = (
                "azure-cognitiveservices-speech is required. "
                "Install with: pip install azure-cognitiveservices-speech"
            )
            metrics.end_time = time.time()
            return [], metrics

        try:
            # Speech SDK auth: key or Entra ID token (transcription)
            if self._config.speech.auth_mode == AuthMode.KEY:
                speech_config = speechsdk.SpeechConfig(
                    subscription=self._config.speech.subscription_key,
                    region=self._config.speech.region,
                )
            else:
                from azure.identity import DefaultAzureCredential
                credential = DefaultAzureCredential()
                token = credential.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                speech_config = speechsdk.SpeechConfig(
                    endpoint=self._config.speech.endpoint,
                )
                speech_config.authorization_token = token.token

            # Set recognition language based on direction
            if self._config.direction == TranslationDirection.AR_TO_EN:
                speech_config.speech_recognition_language = self._config.speech.arabic_locale
            else:
                speech_config.speech_recognition_language = self._config.speech.english_locale

            speech_config.output_format = speechsdk.OutputFormat.Detailed
            speech_config.set_profanity(speechsdk.ProfanityOption.Raw)

            audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
            recogniser = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )

            segments = []
            done = False

            def on_recognized(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    # Extract timing from the result
                    offset_ticks = evt.result.offset       # in 100-nanosecond units
                    duration_ticks = evt.result.duration
                    start_ms = offset_ticks / 10_000       # convert to ms
                    end_ms = (offset_ticks + duration_ticks) / 10_000

                    segments.append(TranscriptionSegment(
                        text=evt.result.text,
                        start_time_ms=start_ms,
                        end_time_ms=end_ms,
                    ))

            def on_canceled(evt):
                nonlocal done
                if evt.reason == speechsdk.CancellationReason.Error:
                    metrics.error = evt.error_details
                done = True

            def on_stopped(evt):
                nonlocal done
                done = True

            recogniser.recognized.connect(on_recognized)
            recogniser.canceled.connect(on_canceled)
            recogniser.session_stopped.connect(on_stopped)

            recogniser.start_continuous_recognition()

            # Wait for recognition to complete
            import threading
            timeout_seconds = 600  # 10 minute max for a single file
            start_wait = time.time()
            while not done and (time.time() - start_wait) < timeout_seconds:
                time.sleep(0.1)

            recogniser.stop_continuous_recognition()

            metrics.output_length = len(segments)
            metrics.end_time = time.time()
            return segments, metrics

        except Exception as e:
            metrics.error = str(e)
            metrics.end_time = time.time()
            return [], metrics

    def _generate_tts(
        self, texts: List[str], output_path: str, target_lang: str
    ) -> StageMetrics:
        """
        Generate text-to-speech audio for translated segments.

        BROADCAST NOTE: TTS quality is secondary to caption accuracy.
        This is an optional enhancement, not a primary deliverable.
        """
        metrics = StageMetrics(stage_name="tts")
        metrics.start_time = time.time()

        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            metrics.error = "azure-cognitiveservices-speech required for TTS"
            metrics.end_time = time.time()
            return metrics

        try:
            # Speech SDK auth: key or Entra ID token (TTS)
            if self._config.speech.auth_mode == AuthMode.KEY:
                speech_config = speechsdk.SpeechConfig(
                    subscription=self._config.speech.subscription_key,
                    region=self._config.speech.region,
                )
            else:
                from azure.identity import DefaultAzureCredential
                credential = DefaultAzureCredential()
                token = credential.get_token(
                    "https://cognitiveservices.azure.com/.default"
                )
                speech_config = speechsdk.SpeechConfig(
                    endpoint=self._config.speech.endpoint,
                )
                speech_config.authorization_token = token.token

            # Select voice based on target language
            if target_lang == "ar":
                speech_config.speech_synthesis_voice_name = self._config.tts.arabic_voice
            else:
                speech_config.speech_synthesis_voice_name = self._config.tts.english_voice

            audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )

            # Concatenate all translated text with pauses
            full_text = " ... ".join(texts)
            result = synthesizer.speak_text(full_text)

            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                metrics.output_length = len(full_text)
            else:
                cancellation = result.cancellation_details
                metrics.error = f"TTS failed: {cancellation.reason} - {cancellation.error_details}"

            metrics.end_time = time.time()
            return metrics

        except Exception as e:
            metrics.error = str(e)
            metrics.end_time = time.time()
            return metrics
