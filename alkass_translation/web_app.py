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
import subprocess
import threading
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
import requests as http_requests

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

# ── Live Stream pipeline globals ──
_stream_pipeline = None
_stream_pipeline_lock = threading.Lock()
_stream_push_stream = None
_ffmpeg_process = None

# ── Channel configuration ──
# Embed URLs from shoof.alkass.net to avoid CORS issues
CHANNELS = [
    {
        "slug": "alkass1",
        "name": "Al Kass One",
        "iframe_url": "https://shoof.alkass.net/live?ch=one",
        "hls_url": os.environ.get(
            "ALKASS1_HLS_URL",
            "https://liveeu-gcps.alkassdigital.net/alkass1-p/20260417T154455Z/mux_video_1080p_ts/hdntl=exp=1776598067~acl=%2f*~data=hdntl~hmac=ab3cf280e47bc10c2f86e670720539cda315812b2bb652b12da45501186c8546/index-1.m3u8",
        ),
    },
    {
        "slug": "alkass2",
        "name": "Al Kass Two",
        "iframe_url": "https://shoof.alkass.net/live?ch=two",
        "hls_url": os.environ.get(
            "ALKASS2_HLS_URL",
            "https://liveeu-gcps.alkassdigital.net/alkass2-p/20260317T165846Z/mux_video_1080p_ts/hdntl=exp=1776598091~acl=%2f*~data=hdntl~hmac=c2631c12bd5180ec5a6fedef7643338eedc44eda9764a4ab65203de40368e70d/index-1.m3u8",
        ),
    },
    {
        "slug": "alkass3",
        "name": "Al Kass Three",
        "iframe_url": "https://shoof.alkass.net/live?ch=three",
        "hls_url": os.environ.get(
            "ALKASS3_HLS_URL",
            "https://liveeu-gcps.alkassdigital.net/alkass3-p/20260417T114153Z/mux_video_1080p_ts/hdntl=exp=1776598106~acl=%2f*~data=hdntl~hmac=6250e6aca5c249e5b3f7d72a177925a34858942a65426c7ee863a23e634df657/index-1.m3u8",
        ),
    },
    {
        "slug": "shooflive",
        "name": "Shoof Live",
        "iframe_url": "https://shoof.alkass.net/live?ch=shoof",
        "hls_url": os.environ.get(
            "SHOOFLIVE_HLS_URL",
            "https://liveeu-gcps.alkassdigital.net/shooflive2/20260410T092615Z/mux_video_1080p_ts/hdntl=exp=1776598160~acl=%2f*~data=hdntl~hmac=4315186ff6316fabab4c7b6d8add74360786761faccf1bc1647c03e7687bb05f/index-1.m3u8",
        ),
    },
]


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
        "speaker_id": event.speaker_id,
        "speaker_label": event.speaker_label,
        "speaker_colour": event.speaker_colour,
    })


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/test-translate")
def test_translate():
    """Debug endpoint to test Translator API auth."""
    import os, json
    from azure.identity import ManagedIdentityCredential
    client_id = os.environ.get("AZURE_CLIENT_ID", "")
    try:
        cred = ManagedIdentityCredential(client_id=client_id) if client_id else None
        if not cred:
            return jsonify({"error": "No AZURE_CLIENT_ID"}), 500
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token.token}",
            "Ocp-Apim-Subscription-Region": os.environ.get("AZURE_TRANSLATOR_REGION", "westeurope"),
            "Ocp-Apim-ResourceId": os.environ.get("AZURE_TRANSLATOR_RESOURCE_ID", ""),
        }
        resp = http_requests.post(
            "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&from=ar&to=en",
            headers=headers,
            json=[{"text": "مرحبا"}],
            timeout=10,
        )
        return jsonify({
            "status": resp.status_code,
            "response": resp.json() if resp.ok else resp.text,
            "region": headers["Ocp-Apim-Subscription-Region"],
            "resource_id": headers["Ocp-Apim-ResourceId"][:50] + "..." if headers["Ocp-Apim-ResourceId"] else "",
            "token_prefix": token.token[:20] + "...",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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


# ═══════════════════════════════════════════════════════════
#  LIVE STREAMS — Channel list & stream transcription
# ═══════════════════════════════════════════════════════════

@app.route("/api/channels")
def get_channels():
    """Return the list of available live stream channels."""
    return jsonify([
        {
            "slug": ch["slug"],
            "name": ch["name"],
            "iframe_url": ch["iframe_url"],
            "hls_url": ch["hls_url"],
        }
        for ch in CHANNELS
    ])


# ── HLS Proxy (avoids CORS from Akamai CDN) ──
_CHANNEL_MAP = {ch["slug"]: ch["hls_url"] for ch in CHANNELS}

@app.route("/api/stream/<slug>/<path:subpath>")
def proxy_stream(slug, subpath):
    """Proxy HLS requests to the upstream CDN to bypass CORS restrictions."""
    base_url = _CHANNEL_MAP.get(slug)
    if not base_url:
        return "Channel not found", 404

    # Build the upstream URL: replace index-1.m3u8 with the requested subpath
    # The base_url ends with .../index-1.m3u8, strip the filename
    upstream_base = base_url.rsplit("/", 1)[0]
    upstream_url = f"{upstream_base}/{subpath}"

    # Use curl_cffi to impersonate Chrome's TLS fingerprint.
    # Akamai Bot Manager blocks non-browser TLS signatures with 403.
    from curl_cffi import requests as curl_requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = curl_requests.get(upstream_url, headers=headers, impersonate="chrome", timeout=15)
    except Exception as e:
        return f"Upstream error: {e}", 502

    if resp.status_code >= 400:
        return f"Upstream returned {resp.status_code}", resp.status_code

    # Rewrite .ts and .m3u8 references in playlists to go through our proxy
    content_type = resp.headers.get("Content-Type", "application/octet-stream")
    if subpath.endswith(".m3u8") or "mpegurl" in content_type.lower():
        body = resp.text
        # Rewrite relative segment/playlist URLs to proxy paths
        lines = body.split("\n")
        rewritten = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                # This is a URI line (segment or sub-playlist)
                if not stripped.startswith("http"):
                    # Relative path — proxy it
                    rewritten.append(f"/api/stream/{slug}/{stripped}")
                else:
                    # Absolute URL — extract path relative to upstream_base and proxy
                    if upstream_base in stripped:
                        rel = stripped[len(upstream_base) + 1:]
                        rewritten.append(f"/api/stream/{slug}/{rel}")
                    else:
                        # Different host — pass through as-is
                        rewritten.append(stripped)
            else:
                rewritten.append(line)
        body = "\n".join(rewritten)
        return Response(body, status=resp.status_code,
                        content_type="application/vnd.apple.mpegurl",
                        headers={"Access-Control-Allow-Origin": "*"})
    else:
        # Binary segment (.ts) — stream through
        return Response(resp.content,
                        status=resp.status_code,
                        content_type=content_type,
                        headers={"Access-Control-Allow-Origin": "*"})


def _stream_caption_to_browser(event: CaptionEvent):
    """Push a stream caption event to all connected browser clients."""
    socketio.emit("stream_caption", {
        "type": event.caption_type.value,
        "source_text": event.source_text,
        "translated_text": event.translated_text,
        "source_language": event.source_language,
        "target_language": event.target_language,
        "latency_ms": round(event.latency_ms),
        "segment_id": event.segment_id,
        "speaker_id": event.speaker_id,
        "speaker_label": event.speaker_label,
        "speaker_colour": event.speaker_colour,
    })


def _stop_ffmpeg():
    """Stop the ffmpeg subprocess if running."""
    global _ffmpeg_process
    if _ffmpeg_process is not None:
        try:
            _ffmpeg_process.terminate()
            _ffmpeg_process.wait(timeout=5)
        except Exception:
            try:
                _ffmpeg_process.kill()
            except Exception:
                pass
        _ffmpeg_process = None


def _ffmpeg_reader_thread(process, push_stream):
    """Read PCM audio from ffmpeg stdout and push to Speech SDK."""
    try:
        while True:
            data = process.stdout.read(4096)
            if not data:
                break
            push_stream.write(data)
    except Exception as e:
        print(f"[ffmpeg reader] Error: {e}")
    finally:
        try:
            push_stream.close()
        except Exception:
            pass


@socketio.on("start_stream_pipeline")
def handle_start_stream(data):
    """Start transcription of a live HLS stream via ffmpeg audio extraction."""
    global _stream_pipeline, _stream_push_stream, _ffmpeg_process

    stream_url = data.get("url", "")
    direction = data.get("direction", "ar-to-en")

    if not stream_url:
        emit("stream_pipeline_error", {"error": "No stream URL provided"})
        return

    with _stream_pipeline_lock:
        # Stop existing stream pipeline if running
        if _stream_pipeline and _stream_pipeline.is_running:
            _stream_pipeline.stop()
        _stop_ffmpeg()

        try:
            import azure.cognitiveservices.speech as speechsdk

            config = PipelineConfig.for_environment("demo")
            config.direction = TranslationDirection(direction)

            glossary_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "glossary_sports.csv"
            )
            if os.path.exists(glossary_path):
                config.glossary_path = glossary_path

            _stream_pipeline = RealTimeTranslationPipeline(config)

            # Set up push audio stream (16kHz, 16-bit, mono PCM)
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1,
            )
            _stream_push_stream = speechsdk.audio.PushAudioInputStream(
                stream_format=audio_format
            )
            audio_config = speechsdk.audio.AudioConfig(
                stream=_stream_push_stream
            )

            # Start ffmpeg to extract audio from HLS stream as 16kHz 16-bit mono PCM
            _ffmpeg_process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-fflags", "nobuffer",
                    "-flags", "low_delay",
                    "-i", stream_url,
                    "-vn",                      # No video
                    "-acodec", "pcm_s16le",     # 16-bit PCM
                    "-ar", "16000",             # 16kHz
                    "-ac", "1",                 # Mono
                    "-f", "s16le",              # Raw PCM output
                    "-",                        # Pipe to stdout
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            # Start a thread to read ffmpeg output and push to Speech SDK
            reader_thread = threading.Thread(
                target=_ffmpeg_reader_thread,
                args=(_ffmpeg_process, _stream_push_stream),
                daemon=True,
            )
            reader_thread.start()

            # Start the pipeline
            _stream_pipeline.start(
                audio_source=audio_config,
                caption_callback=_stream_caption_to_browser,
            )

            emit("status", {
                "message": f"Stream pipeline started: {direction}",
                "running": True,
            })

        except Exception as e:
            _stop_ffmpeg()
            _stream_push_stream = None
            emit("stream_pipeline_error", {"error": str(e)})


@socketio.on("stop_stream_pipeline")
def handle_stop_stream():
    """Stop the stream transcription pipeline and ffmpeg."""
    global _stream_pipeline, _stream_push_stream

    with _stream_pipeline_lock:
        _stop_ffmpeg()

        if _stream_push_stream is not None:
            try:
                _stream_push_stream.close()
            except Exception:
                pass
            _stream_push_stream = None

        if _stream_pipeline and _stream_pipeline.is_running:
            _stream_pipeline.stop()
            emit("status", {"message": "Stream pipeline stopped", "running": False})
        else:
            emit("status", {"message": "Stream pipeline not running", "running": False})


def main():
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  ALKASS TV — Translation Platform Web UI")
    print(f"  Open http://localhost:{port} in your browser")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
