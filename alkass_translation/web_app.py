"""
Web UI for the Alkass Translation Platform.

Serves a branded web interface with real-time caption display
over WebSocket (Socket.IO). The Speech SDK runs server-side and
pushes caption events to all connected browser clients.

Audio can come from the server microphone (local demo) or from
the browser microphone (remote/deployed mode) via WebSocket streaming.

Usage:
    python -m alkass_translation.web_app
"""

import os
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from .config import PipelineConfig, TranslationDirection, AuthMode
from .realtime_pipeline import RealTimeTranslationPipeline, CaptionEvent, CaptionType

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)
app.config["SECRET_KEY"] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=16 * 1024 * 1024)

# Global pipeline reference
_pipeline = None
_pipeline_lock = threading.Lock()

# Push audio stream for browser-based mic input
_push_stream = None


def _caption_to_browser(event: CaptionEvent):
    """Push a caption event to all connected browser clients."""
    socketio.emit("caption", {
        "type": event.caption_type.value,
        "source_text": event.source_text,
        "translated_text": event.translated_text,
        "source_language": event.source_language,
        "target_language": event.target_language,
        "latency_ms": round(event.latency_ms),
        "segment_id": event.segment_id,
    })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    global _pipeline
    return jsonify({
        "running": _pipeline.is_running if _pipeline else False,
    })


@socketio.on("start_pipeline")
def handle_start(data):
    """Start the translation pipeline from a browser request."""
    global _pipeline, _push_stream

    direction = data.get("direction", "ar-to-en")
    env = data.get("env", "demo")
    audio_mode = data.get("audio_mode", "browser")  # "browser" or "server_mic"

    with _pipeline_lock:
        if _pipeline and _pipeline.is_running:
            emit("status", {"message": "Pipeline already running", "running": True})
            return

        config = PipelineConfig.for_environment(env)
        config.direction = TranslationDirection(direction)

        # Load glossary if available
        glossary_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "glossary_sports.csv"
        )
        if os.path.exists(glossary_path):
            config.glossary_path = glossary_path

        _pipeline = RealTimeTranslationPipeline(config)

        try:
            if audio_mode == "browser":
                # Browser sends 16kHz 16-bit mono PCM via WebSocket
                import azure.cognitiveservices.speech as speechsdk

                audio_format = speechsdk.audio.AudioStreamFormat(
                    samples_per_second=16000,
                    bits_per_sample=16,
                    channels=1,
                )
                _push_stream = speechsdk.audio.PushAudioInputStream(
                    stream_format=audio_format
                )
                audio_config = speechsdk.audio.AudioConfig(
                    stream=_push_stream
                )
                _pipeline.start(
                    audio_source=audio_config,
                    caption_callback=_caption_to_browser,
                )
            else:
                # Use server's default microphone
                _push_stream = None
                _pipeline.start(caption_callback=_caption_to_browser)

            emit("status", {
                "message": f"Pipeline started: {direction} ({audio_mode})",
                "running": True,
                "direction": direction,
                "audio_mode": audio_mode,
            })
        except Exception as e:
            _push_stream = None
            emit("pipeline_error", {"error": str(e)})


@socketio.on("audio_data")
def handle_audio_data(data):
    """Receive raw PCM audio chunk from browser and push to Speech SDK."""
    global _push_stream
    if _push_stream is not None and isinstance(data, bytes):
        _push_stream.write(data)


@socketio.on("stop_pipeline")
def handle_stop():
    """Stop the translation pipeline."""
    global _pipeline, _push_stream

    with _pipeline_lock:
        if _push_stream is not None:
            try:
                _push_stream.close()
            except Exception:
                pass
            _push_stream = None

        if _pipeline and _pipeline.is_running:
            _pipeline.stop()
            emit("status", {"message": "Pipeline stopped", "running": False})
        else:
            emit("status", {"message": "Pipeline not running", "running": False})


@socketio.on("connect")
def handle_connect():
    global _pipeline
    running = _pipeline.is_running if _pipeline else False
    emit("status", {"message": "Connected", "running": running})


def main():
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  ALKASS TV — Translation Platform Web UI")
    print(f"  Open http://localhost:{port} in your browser")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
