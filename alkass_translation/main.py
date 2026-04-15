"""
CLI entry point and demo workflow for the Alkass Translation Platform.

Provides two commands:
  1. realtime  — Start near real-time translation (mic or file input)
  2. offline   — Process an audio file for subtitle generation

Usage:
  python -m alkass_translation.main realtime --direction ar-to-en
  python -m alkass_translation.main offline --input match.wav --direction ar-to-en
  python -m alkass_translation.main offline --input match.wav --direction en-to-ar --tts
"""

import argparse
import sys

from .config import PipelineConfig, TranslationDirection


def cmd_realtime(args):
    """Run the near real-time translation pipeline."""
    from .realtime_pipeline import RealTimeTranslationPipeline

    config = PipelineConfig.for_environment(args.env)
    config.direction = TranslationDirection(args.direction)
    if args.glossary:
        config.glossary_path = args.glossary

    pipeline = RealTimeTranslationPipeline(config)

    audio_source = args.input if args.input else None

    print("=" * 60)
    print("  ALKASS TV — Near Real-Time Translation")
    print(f"  Direction: {config.direction.value}")
    print(f"  Environment: {config.environment.value}")
    print(f"  Source: {'microphone' if audio_source is None else audio_source}")
    print("=" * 60)
    print("  Press Ctrl+C to stop.\n")

    try:
        pipeline.start(audio_source=audio_source)
        # Keep running until interrupted or session ends
        import time
        while pipeline.is_running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n  Stopping...")
    finally:
        pipeline.stop()
        print("  Pipeline stopped.")


def cmd_offline(args):
    """Run the offline translation pipeline."""
    from .offline_pipeline import OfflineTranslationPipeline

    config = PipelineConfig.for_environment(args.env)
    config.direction = TranslationDirection(args.direction)
    config.tts.enabled = args.tts
    if args.glossary:
        config.glossary_path = args.glossary

    pipeline = OfflineTranslationPipeline(config)

    print("=" * 60)
    print("  ALKASS TV — Offline Translation Pipeline")
    print(f"  Input: {args.input}")
    print(f"  Direction: {config.direction.value}")
    print(f"  Environment: {config.environment.value}")
    print(f"  TTS: {'enabled' if config.tts.enabled else 'disabled'}")
    print(f"  Formats: {args.formats}")
    print("=" * 60)
    print()

    result = pipeline.process(
        audio_path=args.input,
        output_dir=args.output,
        subtitle_formats=args.formats.split(","),
    )

    # Print summary
    print()
    print("─" * 60)
    print("  RESULTS")
    print("─" * 60)
    print(f"  Segments transcribed: {len(result.segments)}")
    print(f"  Audio duration:       {result.total_duration_ms/1000:.1f}s")
    print(f"  Processing time:      {result.processing_time_s:.1f}s")

    if result.subtitle_path_srt:
        print(f"  SRT output:           {result.subtitle_path_srt}")
    if result.subtitle_path_vtt:
        print(f"  VTT output:           {result.subtitle_path_vtt}")
    if result.tts_audio_path:
        print(f"  TTS audio:            {result.tts_audio_path}")

    if result.errors:
        print(f"\n  ERRORS ({len(result.errors)}):")
        for err in result.errors:
            print(f"    - {err}")

    print()

    # Print stage timing
    print("  STAGE TIMING:")
    for m in result.stage_metrics:
        status = "OK" if not m.error else f"ERROR: {m.error}"
        print(f"    {m.stage_name:20s} {m.duration_ms:8.0f}ms  {status}")
    print("─" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Alkass TV Translation Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Real-time from microphone (Arabic → English)
  python -m alkass_translation.main realtime --direction ar-to-en

  # Real-time from audio file (English → Arabic)
  python -m alkass_translation.main realtime --direction en-to-ar --input commentary.wav

  # Offline: transcribe + translate + generate subtitles
  python -m alkass_translation.main offline --input match.wav --direction ar-to-en

  # Offline with TTS dubbed audio
  python -m alkass_translation.main offline --input match.wav --direction ar-to-en --tts
        """,
    )

    # Common arguments
    parser.add_argument(
        "--env",
        choices=["demo", "poc", "production"],
        default="demo",
        help="Environment configuration profile (default: demo)",
    )
    parser.add_argument(
        "--glossary",
        type=str,
        default=None,
        help="Path to domain glossary CSV file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Pipeline mode")

    # Real-time sub-command
    rt_parser = subparsers.add_parser("realtime", help="Near real-time translation")
    rt_parser.add_argument(
        "--direction",
        choices=["ar-to-en", "en-to-ar"],
        default="ar-to-en",
        help="Translation direction (default: ar-to-en)",
    )
    rt_parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="Audio file path (omit for microphone input)",
    )

    # Offline sub-command
    off_parser = subparsers.add_parser("offline", help="Offline batch translation")
    off_parser.add_argument(
        "--direction",
        choices=["ar-to-en", "en-to-ar"],
        default="ar-to-en",
        help="Translation direction (default: ar-to-en)",
    )
    off_parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to audio/video file",
    )
    off_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output directory (default: same as input file)",
    )
    off_parser.add_argument(
        "--formats",
        type=str,
        default="srt,vtt",
        help="Subtitle formats, comma-separated (default: srt,vtt)",
    )
    off_parser.add_argument(
        "--tts",
        action="store_true",
        help="Generate text-to-speech audio track",
    )

    args = parser.parse_args()

    if args.command == "realtime":
        cmd_realtime(args)
    elif args.command == "offline":
        cmd_offline(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
