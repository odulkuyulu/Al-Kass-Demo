"""
RTMP ingest bridge for the real-time translation pipeline.

This module pulls audio from an RTMP URL with FFmpeg, converts it to
16 kHz mono PCM, and writes chunks into an Azure Speech
PushAudioInputStream.
"""

import shutil
import subprocess
import threading
import time
from typing import Optional


class RtmpAudioIngest:
    """Stream RTMP audio to an Azure Speech PushAudioInputStream."""

    def __init__(
        self,
        rtmp_url: str,
        push_stream,
        ffmpeg_path: str = "ffmpeg",
        rtmp_transport: str = "tcp",
    ):
        self._rtmp_url = rtmp_url
        self._push_stream = push_stream
        self._ffmpeg_path = ffmpeg_path
        self._rtmp_transport = rtmp_transport

        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started_at: Optional[float] = None
        self._last_error: str = ""
        self._reconnect_count = 0
        self._bytes_forwarded = 0
        self._lock = threading.Lock()

    def start(self):
        """Start FFmpeg ingest loop and begin forwarding PCM chunks."""
        binary = shutil.which(self._ffmpeg_path)
        if not binary:
            raise RuntimeError(
                "FFmpeg not found. Install FFmpeg and ensure it is in PATH, "
                "or pass --ffmpeg-path with the full executable path."
            )

        self._running = True
        self._started_at = time.time()
        self._last_error = ""
        self._reconnect_count = 0
        self._bytes_forwarded = 0
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop forwarding and terminate FFmpeg process."""
        self._running = False

        self._terminate_process()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

        try:
            self._push_stream.close()
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        """Return ingest status metadata for diagnostics."""
        with self._lock:
            ffmpeg_alive = self._proc is not None and self._proc.poll() is None
            uptime_s = 0
            if self._started_at:
                uptime_s = int(time.time() - self._started_at)
            return {
                "running": self._running,
                "ffmpeg_alive": ffmpeg_alive,
                "reconnect_count": self._reconnect_count,
                "bytes_forwarded": self._bytes_forwarded,
                "last_error": self._last_error,
                "rtmp_url": self._rtmp_url,
                "rtmp_transport": self._rtmp_transport,
                "uptime_s": uptime_s,
            }

    def _run_loop(self):
        chunk_size = 4096

        while self._running:
            proc = self._start_ffmpeg_process()
            if proc is None:
                self._last_error = "Failed to start FFmpeg process"
                self._running = False
                return

            try:
                if proc.stdout is None:
                    raise RuntimeError("FFmpeg stdout pipe is unavailable")

                while self._running:
                    chunk = proc.stdout.read(chunk_size)
                    if not chunk:
                        break
                    self._push_stream.write(chunk)
                    with self._lock:
                        self._bytes_forwarded += len(chunk)
            except Exception as exc:
                self._last_error = str(exc)

            if not self._running:
                break

            rc = proc.poll()
            if rc not in (None, 0):
                self._last_error = f"FFmpeg exited with code {rc}"

            with self._lock:
                self._reconnect_count += 1

            self._terminate_process()
            time.sleep(1)

    def _start_ffmpeg_process(self) -> Optional[subprocess.Popen]:
        binary = shutil.which(self._ffmpeg_path)
        if not binary:
            return None

        cmd = [
            binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-rw_timeout",
            "15000000",
            "-rtmp_live",
            "live",
            "-rtmp_buffer",
            "100",
            "-rtmp_transport",
            self._rtmp_transport,
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-i",
            self._rtmp_url,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "pipe:1",
        ]

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        return self._proc

    def _terminate_process(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            finally:
                self._proc = None
