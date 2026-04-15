# Alkass TV Translation Platform

Broadcast-grade bilingual audio translation (Arabic ↔ English) for live and post-production workflows.

## Quick Start

### Prerequisites
- Python 3.10+
- Azure Speech Service resource ([create](https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices))
- Azure Translator resource ([create](https://portal.azure.com/#create/Microsoft.CognitiveServicesTextTranslation))

### Install
```bash
pip install -r requirements.txt
```

### Configure — Authentication

The platform supports **two authentication modes**. It auto-detects which to use.

#### Option A: API Keys (if keys are enabled on your subscription)
```powershell
$env:AZURE_SPEECH_KEY = "your-speech-key"
$env:AZURE_SPEECH_REGION = "qatarcentral"
$env:AZURE_TRANSLATOR_KEY = "your-translator-key"
$env:AZURE_TRANSLATOR_REGION = "qatarcentral"
```

#### Option B: Microsoft Entra ID / Token Auth (no keys needed)

If your Azure subscription blocks API keys (e.g., corporate policy), the platform
automatically uses Entra ID token authentication. Just set the region:

```powershell
$env:AZURE_SPEECH_REGION = "qatarcentral"
$env:AZURE_TRANSLATOR_REGION = "qatarcentral"
```

**Prerequisites for Entra auth:**
1. Assign yourself the required roles on each resource (via Portal → Access control (IAM)):
   - Speech resource: **Cognitive Services Speech User**
   - Translator resource: **Cognitive Services User**
2. Sign in to VS Code with your Azure account, or run `az login`
3. The `azure-identity` package handles the rest automatically

### Run

**Near real-time (microphone → captions):**
```bash
python -m alkass_translation.main realtime --direction ar-to-en
```

**Near real-time (file input for testing):**
```bash
python -m alkass_translation.main realtime --direction ar-to-en --input sample.wav
```

**Offline (file → subtitles):**
```bash
python -m alkass_translation.main offline --input match_commentary.wav --direction ar-to-en
```

**Offline with TTS audio and custom glossary:**
```bash
python -m alkass_translation.main offline --input match.wav --direction ar-to-en --tts --glossary glossary_sports.csv
```

## Project Structure

```
alkass_translation/
├── config.py                # Environment-aware configuration
├── observability.py         # Latency tracking, structured logging
├── glossary.py              # Domain-specific term corrections
├── translation_service.py   # Azure Translator API wrapper
├── realtime_pipeline.py     # Near real-time streaming path
├── offline_pipeline.py      # Offline batch processing path
├── subtitles.py             # SRT/VTT subtitle generation
└── main.py                  # CLI entry point

glossary_sports.csv          # Sample sports terminology glossary
ARCHITECTURE.md              # Full reference architecture document
```

## Environment Profiles

Run with `--env` flag to select a configuration profile:

| Profile | Use Case | Max Latency | Log Level |
|---------|----------|-------------|-----------|
| `demo` (default) | Demonstrations, testing | 8s | DEBUG |
| `poc` | Proof of concept | 5s | INFO |
| `production` | Live broadcast | 3s | WARNING |

```bash
python -m alkass_translation.main realtime --env production --direction ar-to-en
```

## Domain Glossary

The sports glossary (`glossary_sports.csv`) ensures consistent translation of broadcast terms: player names, team names, tournament names, and sport-specific vocabulary. It is applied as post-processing after machine translation.

Format: `arabic_term,english_term` (CSV, UTF-8).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full reference architecture, component map, latency budgets, and phase roadmap.
