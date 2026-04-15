"""
Subtitle generator: SRT and VTT format output.

Used by the offline pipeline to produce broadcast-ready subtitle files.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class SubtitleEntry:
    """A single subtitle cue."""
    index: int
    start_time_ms: float    # Start time in milliseconds
    end_time_ms: float      # End time in milliseconds
    text: str               # The subtitle text (translated)
    original_text: str = "" # Optional: source language text


def _format_time_srt(ms: float) -> str:
    """Format milliseconds as SRT timestamp: HH:MM:SS,mmm"""
    total_seconds = ms / 1000.0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    millis = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _format_time_vtt(ms: float) -> str:
    """Format milliseconds as VTT timestamp: HH:MM:SS.mmm"""
    total_seconds = ms / 1000.0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    millis = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def generate_srt(entries: List[SubtitleEntry]) -> str:
    """
    Generate SRT subtitle content.

    SRT format:
        1
        00:00:01,000 --> 00:00:04,000
        This is the subtitle text.

        2
        00:00:05,000 --> 00:00:08,000
        Next subtitle.
    """
    lines = []
    for entry in entries:
        lines.append(str(entry.index))
        start = _format_time_srt(entry.start_time_ms)
        end = _format_time_srt(entry.end_time_ms)
        lines.append(f"{start} --> {end}")
        lines.append(entry.text)
        lines.append("")  # Blank line separator
    return "\n".join(lines)


def generate_vtt(entries: List[SubtitleEntry]) -> str:
    """
    Generate WebVTT subtitle content.

    VTT format:
        WEBVTT

        1
        00:00:01.000 --> 00:00:04.000
        This is the subtitle text.
    """
    lines = ["WEBVTT", ""]
    for entry in entries:
        lines.append(str(entry.index))
        start = _format_time_vtt(entry.start_time_ms)
        end = _format_time_vtt(entry.end_time_ms)
        lines.append(f"{start} --> {end}")
        lines.append(entry.text)
        lines.append("")
    return "\n".join(lines)


def write_subtitles(
    entries: List[SubtitleEntry],
    output_path: str,
    fmt: str = "srt",
):
    """
    Write subtitle entries to a file.

    Args:
        entries: List of SubtitleEntry objects.
        output_path: File path to write.
        fmt: "srt" or "vtt".
    """
    if fmt == "vtt":
        content = generate_vtt(entries)
    else:
        content = generate_srt(entries)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
