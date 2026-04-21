// ────────────────────────────────────────────────────────
//  AL KASS TV — Translation Platform (Client)
// ────────────────────────────────────────────────────────

(function () {
    "use strict";

    // ─── DOM refs ───────────────────────────────────────
    const sourceText      = document.getElementById("sourceText");
    const targetText      = document.getElementById("targetText");
    const historyList     = document.getElementById("historyList");
    const statusBadge     = document.getElementById("statusBadge");
    const statusText      = statusBadge.querySelector(".status-text");
    const latencyValue    = document.getElementById("latencyValue");
    const btnStart        = document.getElementById("btnStart");
    const btnStop         = document.getElementById("btnStop");
    const btnClear        = document.getElementById("clearHistory");
    const selectEnv       = document.getElementById("envSelect");
    const statSegments    = document.getElementById("segmentCount");
    const statAvgLatency  = document.getElementById("avgLatency");
    const statSessionTime = document.getElementById("sessionTime");
    const dirBtns         = document.querySelectorAll(".dir-btn");

    // ─── State ──────────────────────────────────────────
    let direction    = "ar-to-en";
    let running      = false;
    let sessionStart = null;
    let sessionTimer = null;
    let segments     = 0;
    let totalLatency = 0;

    // ─── Audio capture state ────────────────────────────
    let audioContext   = null;
    let mediaStream    = null;
    let audioWorklet   = null;
    let scriptNode     = null;
    const TARGET_SAMPLE_RATE = 16000;

    // ─── Socket.IO ──────────────────────────────────────
    const socket = io();

    socket.on("connect", function () {
        console.log("[WS] Connected:", socket.id);
    });

    socket.on("disconnect", function () {
        console.log("[WS] Disconnected");
        stopAudioCapture();
        setRunning(false);
    });

    socket.on("status", function (data) {
        console.log("[WS] Status:", data);
    });

    // ─── Caption event from server ──────────────────────
    socket.on("caption", function (data) {
        /*
            data = {
                type, source_text, translated_text,
                source_language, target_language,
                latency_ms, segment_id
            }
        */
        const srcLang = data.source_language || "ar";
        const tgtLang = data.target_language || "en";
        const isFinal = data.type === "final";

        // If stream transcription is active, render in stream panel
        if (streamTranscribing) {
            setStreamCaption(streamSourceText, data.source_text, isFinal, false, srcLang);
            setStreamCaption(streamTargetText, data.translated_text, isFinal, true, tgtLang);
            if (data.latency_ms != null && streamLatency) {
                streamLatency.textContent = Math.round(data.latency_ms) + " ms";
            }
            if (isFinal && data.source_text) {
                pushStreamHistory(data, srcLang, tgtLang);
            }
            return;
        }

        // Update current caption (Translation tab)
        setCaption(sourceText, data.source_text, isFinal, false, srcLang);
        setCaption(targetText, data.translated_text, isFinal, true, tgtLang);

        // Latency
        if (data.latency_ms != null) {
            latencyValue.textContent = Math.round(data.latency_ms) + " ms";
        }

        // On final → push to history & update stats
        if (isFinal && data.source_text) {
            pushHistory(data, srcLang, tgtLang);
            segments++;
            totalLatency += data.latency_ms || 0;
            statSegments.textContent = segments;
            statAvgLatency.textContent =
                segments > 0 ? Math.round(totalLatency / segments) + " ms" : "—";
        }
    });

    socket.on("pipeline_error", function (data) {
        console.error("[Pipeline]", data.error);
        stopAudioCapture();
        setRunning(false);
    });

    socket.on("pipeline_stopped", function () {
        stopAudioCapture();
        setRunning(false);
    });

    // ─── Helpers ────────────────────────────────────────
    function setCaption(el, text, isFinal, isTranslated, lang) {
        if (!text) {
            el.innerHTML = '<span class="placeholder">' +
                (isTranslated ? "Translation will appear here…" : "Listening…") +
                "</span>";
            el.className = "caption-text";
            return;
        }
        el.textContent = text;
        el.dir = lang === "ar" ? "rtl" : "ltr";
        el.style.textAlign = lang === "ar" ? "right" : "left";

        let cls = "caption-text";
        cls += isFinal ? " final" : " partial";
        if (isTranslated) cls += " translated";
        el.className = cls;
    }

    function pushHistory(data, srcLang, tgtLang) {
        const item = document.createElement("div");
        item.className = "history-item";

        const src = document.createElement("div");
        src.className = "hi-source";
        src.textContent = data.source_text;
        src.dir = srcLang === "ar" ? "rtl" : "ltr";
        src.style.textAlign = srcLang === "ar" ? "right" : "left";

        const arrow = document.createElement("div");
        arrow.className = "hi-arrow";
        arrow.textContent = "→";

        const tgt = document.createElement("div");
        tgt.className = "hi-target";
        tgt.textContent = data.translated_text;
        tgt.dir = tgtLang === "ar" ? "rtl" : "ltr";
        tgt.style.textAlign = tgtLang === "ar" ? "right" : "left";

        const lat = document.createElement("div");
        lat.className = "hi-latency";
        lat.textContent = data.latency_ms ? Math.round(data.latency_ms) + "ms" : "";

        item.appendChild(src);
        item.appendChild(arrow);
        item.appendChild(tgt);
        item.appendChild(lat);

        // Prepend (newest on top)
        historyList.insertBefore(item, historyList.firstChild);

        // Limit items
        while (historyList.children.length > 200) {
            historyList.removeChild(historyList.lastChild);
        }
    }

    function setRunning(state) {
        running = state;
        btnStart.disabled = state;
        btnStop.disabled = !state;

        if (state) {
            statusBadge.classList.add("live");
            statusText.textContent = "LIVE";
        } else {
            statusBadge.classList.remove("live");
            statusText.textContent = "IDLE";
            clearInterval(sessionTimer);
        }
    }

    function startSessionClock() {
        sessionStart = Date.now();
        clearInterval(sessionTimer);
        sessionTimer = setInterval(function () {
            const secs = Math.floor((Date.now() - sessionStart) / 1000);
            const m = Math.floor(secs / 60).toString().padStart(2, "0");
            const s = (secs % 60).toString().padStart(2, "0");
            statSessionTime.textContent = m + ":" + s;
        }, 1000);
    }

    function resetStats() {
        segments = 0;
        totalLatency = 0;
        statSegments.textContent = "0";
        statAvgLatency.textContent = "—";
        statSessionTime.textContent = "00:00";
        latencyValue.textContent = "— ms";
    }

    // ─── Browser audio capture ────────────────────────────
    function downsampleBuffer(buffer, inputRate, outputRate) {
        if (inputRate === outputRate) return buffer;
        var ratio = inputRate / outputRate;
        var newLength = Math.round(buffer.length / ratio);
        var result = new Float32Array(newLength);
        var offsetResult = 0;
        var offsetBuffer = 0;
        while (offsetResult < result.length) {
            var nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
            var accum = 0, count = 0;
            for (var i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
                accum += buffer[i];
                count++;
            }
            result[offsetResult] = accum / count;
            offsetResult++;
            offsetBuffer = nextOffsetBuffer;
        }
        return result;
    }

    function floatTo16BitPCM(samples) {
        var buffer = new ArrayBuffer(samples.length * 2);
        var view = new DataView(buffer);
        for (var i = 0; i < samples.length; i++) {
            var s = Math.max(-1, Math.min(1, samples[i]));
            view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        }
        return buffer;
    }

    async function startAudioCapture() {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: TARGET_SAMPLE_RATE,
                    echoCancellation: true,
                    noiseSuppression: true,
                }
            });
        } catch (err) {
            console.error("[Audio] Microphone access denied:", err);
            alert("Microphone access is required for live translation.\nPlease allow microphone access and try again.");
            return false;
        }

        audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: TARGET_SAMPLE_RATE,
        });

        var source = audioContext.createMediaStreamSource(mediaStream);
        var bufferSize = 4096;

        // Use ScriptProcessorNode (widely supported)
        scriptNode = audioContext.createScriptProcessor(bufferSize, 1, 1);
        scriptNode.onaudioprocess = function (e) {
            if (!running) return;
            var inputData = e.inputBuffer.getChannelData(0);
            var downsampled = downsampleBuffer(
                inputData, audioContext.sampleRate, TARGET_SAMPLE_RATE
            );
            var pcm16 = floatTo16BitPCM(downsampled);
            socket.emit("audio_data", pcm16);
        };

        source.connect(scriptNode);
        scriptNode.connect(audioContext.destination);

        console.log("[Audio] Capture started, sampleRate=" + audioContext.sampleRate);
        return true;
    }

    function stopAudioCapture() {
        if (scriptNode) {
            scriptNode.disconnect();
            scriptNode = null;
        }
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }
        if (mediaStream) {
            mediaStream.getTracks().forEach(function (t) { t.stop(); });
            mediaStream = null;
        }
        console.log("[Audio] Capture stopped");
    }

    // ─── Controls ───────────────────────────────────────
    btnStart.addEventListener("click", async function () {
        if (running) return;
        resetStats();

        // Start browser audio capture first
        var ok = await startAudioCapture();
        if (!ok) return;

        setRunning(true);
        startSessionClock();

        // Clear current captions
        setCaption(sourceText, "", false, false, "ar");
        setCaption(targetText, "", false, true, "en");

        socket.emit("start_pipeline", {
            direction: direction,
            env: selectEnv.value,
            audio_mode: "browser"
        });
    });

    btnStop.addEventListener("click", function () {
        if (!running) return;
        stopAudioCapture();
        socket.emit("stop_pipeline");
        setRunning(false);
    });

    btnClear.addEventListener("click", function () {
        historyList.innerHTML = "";
    });

    // Direction buttons
    dirBtns.forEach(function (btn) {
        btn.addEventListener("click", function () {
            if (running) return; // can't switch while live
            dirBtns.forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            direction = btn.dataset.direction;

            // Update caption block labels dynamically
            var srcLabel = document.getElementById("sourceLangLabel");
            var tgtLabel = document.getElementById("targetLangLabel");
            var srcFlag  = document.getElementById("sourceLangFlag");
            var tgtFlag  = document.getElementById("targetLangFlag");
            if (direction === "ar-to-en") {
                if (srcLabel) srcLabel.textContent = "Arabic — Source";
                if (tgtLabel) tgtLabel.textContent = "English — Translation";
                if (srcFlag) srcFlag.textContent = "🇶🇦";
                if (tgtFlag) tgtFlag.textContent = "🇬🇧";
            } else {
                if (srcLabel) srcLabel.textContent = "English — Source";
                if (tgtLabel) tgtLabel.textContent = "Arabic — Translation";
                if (srcFlag) srcFlag.textContent = "🇬🇧";
                if (tgtFlag) tgtFlag.textContent = "🇶🇦";
            }
        });
    });

    // ─── Init ───────────────────────────────────────────
    setRunning(false);

    // ═══════════════════════════════════════════════════════
    //  TAB SWITCHING
    // ═══════════════════════════════════════════════════════
    var tabBtns = document.querySelectorAll(".tab-btn");
    var tabPanels = document.querySelectorAll(".tab-panel");

    tabBtns.forEach(function (btn) {
        btn.addEventListener("click", function () {
            tabBtns.forEach(function (b) { b.classList.remove("active"); });
            tabPanels.forEach(function (p) { p.classList.remove("active"); });
            btn.classList.add("active");
            var panel = document.getElementById("panel-" + btn.dataset.tab);
            if (panel) panel.classList.add("active");
        });
    });

    // ═══════════════════════════════════════════════════════
    //  LIVE STREAMS TAB
    // ═══════════════════════════════════════════════════════
    var channelList        = document.getElementById("channelList");
    var videoPlaceholder   = document.getElementById("videoPlaceholder");
    var streamPlayer       = document.getElementById("streamPlayer");
    var streamChannelName  = document.getElementById("streamChannelName");
    var streamStatusBadge  = document.getElementById("streamStatusBadge");
    var streamStatusText   = streamStatusBadge ? streamStatusBadge.querySelector(".status-text") : null;
    var streamSourceText   = document.getElementById("streamSourceText");
    var streamTargetText   = document.getElementById("streamTargetText");
    var streamLatency      = document.getElementById("streamLatency");
    var streamSrcFlag      = document.getElementById("streamSrcFlag");
    var streamTgtFlag      = document.getElementById("streamTgtFlag");
    var btnStartTranscribe = document.getElementById("btnStartTranscribe");
    var btnStopTranscribe  = document.getElementById("btnStopTranscribe");
    var clearStreamHistory = document.getElementById("clearStreamHistory");
    var streamHistoryList  = document.getElementById("streamHistoryList");
    var streamDirBtns      = document.querySelectorAll("#streamDirectionToggle .dir-btn");

    var activeChannel      = null;
    var hlsPlayer          = null;
    var streamDirection    = "ar-to-en";
    var streamTranscribing = false;

    // ─── Load channels ──────────────────────────────────
    function loadChannels() {
        fetch("/api/channels")
            .then(function (r) { return r.json(); })
            .then(function (channels) {
                channelList.innerHTML = "";
                channels.forEach(function (ch) {
                    var item = document.createElement("div");
                    item.className = "channel-item";
                    item.dataset.slug = ch.slug;
                    item.innerHTML =
                        '<span class="channel-dot"></span>' +
                        '<span class="channel-name">' + ch.name + '</span>';
                    item.addEventListener("click", function () {
                        selectChannel(ch, item);
                    });
                    channelList.appendChild(item);
                });
            })
            .catch(function (err) {
                console.error("[Channels] Failed to load:", err);
            });
    }

    // ─── Select channel ─────────────────────────────────
    function selectChannel(ch, el) {
        // Stop current transcription if running
        if (streamTranscribing) {
            stopStreamTranscription();
        }

        // Update active state
        document.querySelectorAll(".channel-item").forEach(function (c) {
            c.classList.remove("active");
        });
        el.classList.add("active");
        activeChannel = ch;
        streamChannelName.textContent = ch.name;

        // Open stream in popup window (avoids third-party cookie restrictions)
        openStreamPopup(ch.iframe_url, ch.name);
    }

    // ─── Popup Stream Player ────────────────────────────
    var streamActiveMsg = document.getElementById("streamActiveMsg");
    var activeChannelLabel = document.getElementById("activeChannelLabel");
    var btnReopenStream = document.getElementById("btnReopenStream");
    var streamPopup = null;

    function openStreamPopup(url, channelName) {
        // Close existing popup
        if (streamPopup && !streamPopup.closed) {
            streamPopup.close();
        }

        // Hide placeholder, show active message
        videoPlaceholder.classList.add("hidden");
        streamPlayer.style.display = "none";
        streamActiveMsg.style.display = "block";
        activeChannelLabel.textContent = channelName;

        // Open popup window
        var w = 800, h = 500;
        var left = (screen.width - w) / 2;
        var top = (screen.height - h) / 2;
        streamPopup = window.open(
            url,
            "alkass_stream",
            "width=" + w + ",height=" + h + ",left=" + left + ",top=" + top + ",resizable=yes,scrollbars=no"
        );
    }

    if (btnReopenStream) {
        btnReopenStream.addEventListener("click", function () {
            if (activeChannel) {
                openStreamPopup(activeChannel.iframe_url, activeChannel.name);
            }
        });
    }

    // ─── Stream transcription controls ──────────────────
    btnStartTranscribe.addEventListener("click", function () {
        if (!activeChannel || streamTranscribing) return;
        startStreamTranscription();
    });

    btnStopTranscribe.addEventListener("click", function () {
        if (!streamTranscribing) return;
        stopStreamTranscription();
    });

    // ─── Stream audio capture state ────────────────────
    var streamAudioContext = null;
    var streamMediaStream = null;
    var streamScriptNode = null;

    async function startStreamAudioCapture() {
        try {
            streamMediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: TARGET_SAMPLE_RATE,
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                }
            });
        } catch (err) {
            console.error("[StreamAudio] Microphone access denied:", err);
            alert("Microphone access is required for live translation.\nPlease allow microphone access and try again.\n\nTip: Play the stream through speakers (not headphones) so the mic can pick up the audio.");
            return false;
        }

        streamAudioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: TARGET_SAMPLE_RATE,
        });

        var source = streamAudioContext.createMediaStreamSource(streamMediaStream);
        var bufferSize = 4096;

        streamScriptNode = streamAudioContext.createScriptProcessor(bufferSize, 1, 1);
        streamScriptNode.onaudioprocess = function (e) {
            if (!streamTranscribing) return;
            var inputData = e.inputBuffer.getChannelData(0);
            var downsampled = downsampleBuffer(
                inputData, streamAudioContext.sampleRate, TARGET_SAMPLE_RATE
            );
            var pcm16 = floatTo16BitPCM(downsampled);
            socket.emit("audio_data", pcm16);
        };

        source.connect(streamScriptNode);
        streamScriptNode.connect(streamAudioContext.destination);

        console.log("[StreamAudio] Capture started, sampleRate=" + streamAudioContext.sampleRate);
        return true;
    }

    function stopStreamAudioCapture() {
        if (streamScriptNode) {
            streamScriptNode.disconnect();
            streamScriptNode = null;
        }
        if (streamAudioContext) {
            streamAudioContext.close();
            streamAudioContext = null;
        }
        if (streamMediaStream) {
            streamMediaStream.getTracks().forEach(function (t) { t.stop(); });
            streamMediaStream = null;
        }
        console.log("[StreamAudio] Capture stopped");
    }

    async function startStreamTranscription() {
        // Start browser audio capture first
        var ok = await startStreamAudioCapture();
        if (!ok) return;

        streamTranscribing = true;
        btnStartTranscribe.disabled = true;
        btnStopTranscribe.disabled = false;
        if (streamStatusBadge) streamStatusBadge.classList.add("live");
        if (streamStatusText) streamStatusText.textContent = "LIVE";

        // Clear current transcript
        setStreamCaption(streamSourceText, "", false, false, "ar");
        setStreamCaption(streamTargetText, "", false, true, "en");

        // Use browser mic audio mode (same as Translation tab)
        socket.emit("start_pipeline", {
            direction: streamDirection,
            env: "demo",
            audio_mode: "browser",
        });
    }

    function stopStreamTranscription() {
        stopStreamAudioCapture();
        streamTranscribing = false;
        btnStartTranscribe.disabled = false;
        btnStopTranscribe.disabled = true;
        if (streamStatusBadge) streamStatusBadge.classList.remove("live");
        if (streamStatusText) streamStatusText.textContent = "Offline";

        socket.emit("stop_pipeline");
    }

    // ─── Stream caption rendering ───────────────────────
    socket.on("stream_caption", function (data) {
        if (!streamTranscribing) return;

        var srcLang = data.source_language || "ar";
        var tgtLang = data.target_language || "en";
        var isFinal = data.type === "final";

        setStreamCaption(streamSourceText, data.source_text, isFinal, false, srcLang);
        setStreamCaption(streamTargetText, data.translated_text, isFinal, true, tgtLang);

        if (data.latency_ms != null) {
            streamLatency.textContent = Math.round(data.latency_ms) + " ms";
        }

        if (isFinal && data.source_text) {
            pushStreamHistory(data, srcLang, tgtLang);
        }
    });

    socket.on("stream_pipeline_error", function (data) {
        console.error("[Stream Pipeline]", data.error);
        stopStreamTranscription();
    });

    socket.on("stream_pipeline_stopped", function () {
        stopStreamTranscription();
    });

    function setStreamCaption(el, text, isFinal, isTranslated, lang) {
        if (!text) {
            el.innerHTML = '<span class="placeholder">' +
                (isTranslated ? "Translation will appear here…" : "Waiting for transcription…") +
                "</span>";
            el.className = "transcript-text";
            if (isTranslated) el.className += " translated";
            return;
        }
        el.textContent = text;
        el.dir = lang === "ar" ? "rtl" : "ltr";
        el.style.textAlign = lang === "ar" ? "right" : "left";
        var cls = "transcript-text";
        if (isTranslated) cls += " translated";
        cls += isFinal ? " final" : " partial";
        el.className = cls;
    }

    function pushStreamHistory(data, srcLang, tgtLang) {
        var item = document.createElement("div");
        item.className = "history-item";

        var src = document.createElement("div");
        src.className = "hi-source";
        src.textContent = data.source_text;
        src.dir = srcLang === "ar" ? "rtl" : "ltr";
        src.style.textAlign = srcLang === "ar" ? "right" : "left";

        var arrow = document.createElement("div");
        arrow.className = "hi-arrow";
        arrow.textContent = "→";

        var tgt = document.createElement("div");
        tgt.className = "hi-target";
        tgt.textContent = data.translated_text;
        tgt.dir = tgtLang === "ar" ? "rtl" : "ltr";
        tgt.style.textAlign = tgtLang === "ar" ? "right" : "left";

        var lat = document.createElement("div");
        lat.className = "hi-latency";
        lat.textContent = data.latency_ms ? Math.round(data.latency_ms) + "ms" : "";

        item.appendChild(src);
        item.appendChild(arrow);
        item.appendChild(tgt);
        item.appendChild(lat);

        streamHistoryList.insertBefore(item, streamHistoryList.firstChild);
        while (streamHistoryList.children.length > 200) {
            streamHistoryList.removeChild(streamHistoryList.lastChild);
        }
    }

    if (clearStreamHistory) {
        clearStreamHistory.addEventListener("click", function () {
            streamHistoryList.innerHTML = "";
        });
    }

    // ─── Stream direction toggle ────────────────────────
    streamDirBtns.forEach(function (btn) {
        btn.addEventListener("click", function () {
            if (streamTranscribing) return;
            streamDirBtns.forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            streamDirection = btn.dataset.direction;

            if (streamDirection === "ar-to-en") {
                if (streamSrcFlag) streamSrcFlag.textContent = "🇶🇦";
                if (streamTgtFlag) streamTgtFlag.textContent = "🇬🇧";
            } else {
                if (streamSrcFlag) streamSrcFlag.textContent = "🇬🇧";
                if (streamTgtFlag) streamTgtFlag.textContent = "🇶🇦";
            }
        });
    });

    // ─── Load channels on startup ───────────────────────
    loadChannels();

})();
