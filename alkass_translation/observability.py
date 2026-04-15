"""
Pipeline observability: latency tracking, structured logging, metrics.

Every pipeline stage records timing so operators can identify bottlenecks.
This is critical in broadcast where latency budgets are strict.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StageMetrics:
    """Captures timing for a single pipeline stage execution."""
    stage_name: str
    start_time: float = 0.0
    end_time: float = 0.0
    input_length: int = 0       # characters or audio-ms, depending on stage
    output_length: int = 0
    error: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0


@dataclass
class SegmentTrace:
    """
    End-to-end trace for a single segment of audio/text through the pipeline.
    A segment is one recognisable unit (partial or final recognition result).
    """
    segment_id: str = ""
    source_language: str = ""
    target_language: str = ""
    source_text: str = ""
    translated_text: str = ""
    stages: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def total_latency_ms(self) -> float:
        if not self.stages:
            return 0.0
        first_start = min(s.start_time for s in self.stages)
        last_end = max(s.end_time for s in self.stages if s.end_time)
        return (last_end - first_start) * 1000

    def add_stage(self, stage: StageMetrics):
        self.stages.append(stage)

    def to_log_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "source_text_preview": self.source_text[:80],
            "translated_text_preview": self.translated_text[:80],
            "total_latency_ms": round(self.total_latency_ms, 1),
            "stages": [
                {
                    "name": s.stage_name,
                    "duration_ms": round(s.duration_ms, 1),
                    "error": s.error,
                }
                for s in self.stages
            ],
        }


class PipelineLogger:
    """Structured logger for the translation pipeline."""

    def __init__(self, name: str = "alkass.pipeline", level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            fmt = logging.Formatter(
                "[%(asctime)s] %(name)s %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(fmt)
            self.logger.addHandler(handler)

    def log_segment(self, trace: SegmentTrace, max_latency_ms: float):
        """Log a completed segment trace with latency warning if over budget."""
        latency = trace.total_latency_ms
        log_data = trace.to_log_dict()

        if latency > max_latency_ms:
            self.logger.warning(
                "LATENCY EXCEEDED: %.0fms (budget: %.0fms) | %s",
                latency, max_latency_ms, log_data,
            )
        else:
            self.logger.info(
                "Segment processed: %.0fms | %s", latency, log_data
            )

    def log_error(self, stage: str, error: str, context: dict = None):
        self.logger.error("Stage [%s] error: %s | context=%s", stage, error, context)

    def log_info(self, message: str):
        self.logger.info(message)

    def log_debug(self, message: str):
        self.logger.debug(message)
