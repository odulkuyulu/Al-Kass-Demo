# Alkass TV Translation Platform — Reference Architecture

## Overview

A broadcast-grade bilingual translation system (Arabic ↔ English) with two processing paths sharing common components. Built on Azure Cognitive Services.

---

## Logical Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ALKASS TRANSLATION PLATFORM                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     INPUT LAYER                              │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐     │   │
│  │  │ Mic/Line │  │ Audio File   │  │ Network Stream     │     │   │
│  │  │ Input    │  │ (.wav/.mp3)  │  │ (RTMP/SRT/NDI)     │     │   │
│  │  └────┬─────┘  └──────┬───────┘  └─────────┬──────────┘     │   │
│  │       │               │                     │                │   │
│  │       └───────────┬───┴─────────────────────┘                │   │
│  └───────────────────┼──────────────────────────────────────────┘   │
│                      │                                              │
│          ┌───────────┴───────────┐                                  │
│          │                       │                                  │
│  ┌───────▼────────┐    ┌────────▼─────────┐                        │
│  │  NEAR REAL-TIME │    │  OFFLINE / BATCH  │                       │
│  │     PATH        │    │     PATH          │                       │
│  │                 │    │                   │                       │
│  │ Streaming STT   │    │ Full-file STT     │                       │
│  │ (continuous     │    │ (complete         │                       │
│  │  recognition)   │    │  transcription)   │                       │
│  │      │          │    │      │            │                       │
│  │      ▼          │    │      ▼            │                       │
│  │ Partial + Final │    │ Segmented text    │                       │
│  │ events          │    │ with timestamps   │                       │
│  │      │          │    │      │            │                       │
│  └──────┼──────────┘    └──────┼────────────┘                       │
│         │                      │                                    │
│  ┌──────▼──────────────────────▼────────────────────────────────┐   │
│  │                   SHARED SERVICES                            │   │
│  │                                                              │   │
│  │  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐   │   │
│  │  │ Translation     │  │ Domain       │  │ Observability │   │   │
│  │  │ Service         │  │ Glossary     │  │ (logging,     │   │   │
│  │  │ (Azure          │  │ (sports      │  │  latency,     │   │   │
│  │  │  Translator)    │  │  terms)      │  │  metrics)     │   │   │
│  │  └─────────────────┘  └──────────────┘  └───────────────┘   │   │
│  │                                                              │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         │                                           │
│  ┌──────────────────────▼───────────────────────────────────────┐   │
│  │                     OUTPUT LAYER                             │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────┐  ┌──────────────┐    │   │
│  │  │ Live     │  │ SRT/VTT  │  │ TTS  │  │ Broadcast    │    │   │
│  │  │ Captions │  │ Subtitle │  │Audio │  │ Overlay API  │    │   │
│  │  │ (stream) │  │ Files    │  │Track │  │ (TODO Ph.2)  │    │   │
│  │  └──────────┘  └──────────┘  └──────┘  └──────────────┘    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Processing Paths

### 1. Near Real-Time Path

```
Audio Stream ──► ConversationTranscriber ──► Partial / Final + speaker_id ──► Translator ──► Glossary ──► Caption Output
                  (diarised streaming STT)        (per utterance)              (per segment)   (post-process)  (callback w/ persona)
```

**Latency budget (target):**

| Stage | Target | Notes |
|-------|--------|-------|
| STT + diarisation | < 800ms | ConversationTranscriber adds 150–400 ms over plain STT |
| Translation API | < 800ms | Single-segment call |
| Glossary + emit | < 50ms | In-memory lookup |
| **End-to-end** | **< 2s** | Production config |

**Key design decisions:**
- `ConversationTranscriber` performs **unsupervised diarisation** — no voice
  enrolment, no pre-known speakers; personas are clustered per session.
- Partial results are translated immediately (flicker vs latency trade-off)
  but rendered **without a speaker badge** until finalised, to avoid colour
  flicker as diarisation confidence builds.
- Final results carry a stable `Speaker N` persona via `SpeakerRegistry`.
- No sentence-boundary blocking — low latency is prioritised.
- Latency per segment is logged for operational visibility.

### 2. Offline Path

```
Audio File ──► Azure Speech SDK ──► Full Transcription ──► Batch Translation ──► Subtitle Gen ──► SRT/VTT Files
               (batch recognition)   (timestamped            (Azure Translator     (with timing)
                                      segments)               batch API)

                                                         ──► TTS (optional) ──► Audio Track
```

**Key design decisions:**
- Full transcription before translation (accuracy over speed)
- Batch translation API for efficiency (up to 100 segments per call)
- Timing data preserved from STT for accurate subtitle timestamps
- TTS is optional and secondary to subtitle output

---

## Azure Services Used

| Service | Purpose | SKU Recommendation |
|---------|---------|-------------------|
| Azure Speech Service | STT (streaming + batch), TTS | S0 Standard |
| Azure Translator | Text translation (AR ↔ EN) | S1 Standard |

Region: **Qatar Central** (lowest latency to Doha).

---

## Component Map

```
alkass_translation/
├── __init__.py              # Package marker
├── __main__.py              # Module entry point
├── config.py                # All configuration (env-aware)
├── observability.py         # Latency tracking, structured logging
├── glossary.py              # Domain term corrections
├── speakers.py              # SpeakerRegistry: diarised IDs → personas + colours
├── translation_service.py   # Azure Translator wrapper (shared)
├── realtime_pipeline.py     # Near real-time streaming path (ConversationTranscriber)
├── offline_pipeline.py      # Offline batch path + subtitle gen
├── subtitles.py             # SRT/VTT format generation
├── web_app.py               # Flask + Socket.IO UI (live captions, channels)
└── main.py                  # CLI entry point
```

---

## Speaker Diarisation

### Goal

Identify *who is speaking* in a single mixed audio stream and label each
caption with a stable persona (movie-subtitle convention: `Speaker 1`,
`Speaker 2`, …). No voice enrolment is required.

### Flow

```
HLS / Mic input ──► ConversationTranscriber ──► Guest_1 / Guest_2 / Unknown
                     (auto-cluster voices)
                              │
                              ▼
                       SpeakerRegistry  ──► Persona { id: "S1", label: "Speaker 1", colour: "#FFD400" }
                  (session-scoped map)
                              │
                              ▼
                    Translator + Glossary
                              │
                              ▼
                  CaptionEvent (+ speaker fields)
                              │
                              ▼
                Web UI: coloured pill + left bar
```

### Persona palette

| Slot | Colour | Hex |
|------|--------|-----|
| Speaker 1 | Alkass gold | `#FFD400` |
| Speaker 2 | Cyan        | `#00C2FF` |
| Speaker 3 | Coral       | `#FF6B6B` |
| Speaker 4 | Mint        | `#7CFFB2` |
| Speaker 5 | Lilac       | `#C792EA` |
| Speaker 6 | Amber       | `#FFA552` |
| Unknown   | Neutral grey | `#9AA0A6` |

### Behaviour

- **Session scope:** persona numbering resets to 1 on every pipeline start.
- **Partials:** rendered without badge / colour bar to avoid flicker.
- **Finals:** carry the resolved persona on screen and in the history list.
- **Trade-off:** ConversationTranscriber adds ~150–400 ms over plain STT;
  fits inside the production profile latency budget.

### When this is not enough

| Scenario | Recommended upgrade |
|----------|---------------------|
| Need real names ("Khalid Jassim") | Add Azure **Speaker Recognition** with voice enrolment |
| Heavy overlapping speech | Self-host **pyannote.audio** / **WhisperX** (GPU, +1–3 s latency) |
| Multi-mic OB feed available | Skip diarisation — tag by **channel index**, deterministic |

---

## Configuration Profiles

| Parameter | Demo | PoC | Production |
|-----------|------|-----|------------|
| Caption emit interval | 400ms | 500ms | 300ms |
| Stabilisation window | 300ms | 300ms | 200ms |
| Max acceptable latency | 8000ms | 5000ms | 3000ms |
| Translation timeout | 3s | 3s | 2s |
| Log level | DEBUG | INFO | WARNING |

---

## Phase Roadmap

### Phase 1 (Current) — PoC / Demo
- [x] Streaming STT with Azure Speech SDK
- [x] Bidirectional translation (AR ↔ EN)
- [x] Domain glossary post-processing
- [x] Near real-time caption output
- [x] Offline transcription + translation
- [x] SRT/VTT subtitle generation
- [x] Optional TTS audio track
- [x] Per-stage latency observability
- [x] Environment-aware configuration
- [x] Unsupervised speaker diarisation (ConversationTranscriber + coloured personas)

### Phase 2 — Production Hardening
- [ ] Named-speaker identification (Azure Speaker Recognition + enrolment)
- [ ] Operator UI to rename personas ("Speaker 2" → "Commentator A")
- [ ] Diarised SRT/VTT output (offline path)
- [ ] Broadcast overlay integration (CG/graphics API)
- [ ] Session persistence and recovery
- [ ] Network stream input (RTMP, SRT, NDI)
- [ ] Multi-channel concurrent processing
- [ ] Custom terminology model training (Azure Custom Translator)
- [ ] Horizontal scaling for multiple simultaneous feeds
- [ ] Health check and watchdog endpoints
- [ ] Automated quality scoring (BLEU/WER metrics)

### Phase 3 — Extended Capabilities
- [ ] Additional language pairs (French, Spanish, Urdu)
- [ ] VOD pipeline integration
- [ ] Automated highlight detection + translation
- [ ] Cloud-native deployment (AKS / Container Apps)
- [ ] Real-time dashboard for operators
- [ ] A/B testing framework for translation quality
- [ ] Integration with MAM (Media Asset Management) systems

---

## Assumptions & Limitations

1. **No zero-latency guarantee.** Near real-time means seconds of delay, not milliseconds. This is acceptable for caption overlay but not for live lip-sync dubbing.

2. **Single language pair per pipeline instance.** To translate both directions simultaneously, run two pipeline instances.

3. **No speaker identification in Phase 1.** Personas are anonymous
   (`Speaker 1`, `Speaker 2`, …) and reset every session. To map a persona
   to a real person across sessions, Phase 2 adds Azure Speaker Recognition
   with voice enrolment.

4. **Translation quality depends on Azure Translator.** Domain glossary helps with sports terms, but novel phrases will use generic translation. Phase 2 adds Custom Translator training.

5. **Audio quality matters.** Background crowd noise, overlapping speech, and low-quality microphones will degrade STT accuracy. The system does not perform audio enhancement.

6. **File format support** depends on Azure Speech SDK capabilities (WAV, MP3, OGG, FLAC).
