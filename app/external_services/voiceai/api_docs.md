# FluentMeet Voice.ai Integration Documentation

> **Package Location:** `/app/external_services/voiceai`
> **Purpose:** Handles external asynchronous integrations with the Voice.ai Text-to-Speech Generation API.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Public API (`service.py`)](#public-api-servicepy)
- [Format & Model Targeting](#format--model-targeting)
- [Configuration](#configuration)

---

## Overview

The `app/external_services/voiceai` package acts as the active backend for stage 4 of the real-time audio pipeline. It intercepts translated text streams and synthesizes them into dynamic real-time human voices using the Voice.ai `/api/v1/tts/speech` endpoints. Note that this package runs dynamically as an alternative to OpenAI depending on standard environment configurations (`ACTIVE_TTS_PROVIDER="voiceai"`).

---

## Architecture

This service acts identically to the OpenAI SDK. To maintain tight coupling with core architectures, ignoring bulk Python packages, it resolves all remote calls using `httpx.AsyncClient` blocks statelessly.

The configuration relies on environment variables, pulling `VOICEAI_TTS_MODEL` and configuring payload definitions instantly per-request.

---

## Public API (`service.py`)

### `VoiceAITTSService` 

The fully asynchronous service layer encapsulated via Singleton pattern mapping logic to `/tts/speech`.

#### `synthesize(text, language, voice_id, encoding)`
Initiates asynchronous remote calls to stream speech endpoints.
*   **Args:**
    *   `text` *(str)*: Target string text block mapped to conversion.
    *   `language` *(str)*: Native mapping used specifically by Voice.ai context engines (e.g., swapping to multilingual vs english default models automatically).
    *   `voice_id` *(str, optional)*: An explicit ID tag generated via Voice.ai console for custom cloned models. Defaults to default models if None.
    *   `encoding` *(str)*: Encoding request (`"linear16"` or `"opus"`).
*   **Returns:**
    Returns a unified `dict` format identical to OpenAI payload structures, guaranteeing seamless swapping inside caller DAEMONS without syntax rewrites.
    ```json
    {
      "audio_bytes": "\\x01\\x00\\xFF...",
      "sample_rate": 16000,
      "latency_ms": 352.1
    }
    ```
*   **Exception Behavior:** Immediately traps non-200 configurations routing `httpx.HTTPStatusError` directly to Kafka Retry protocols.

---

## Format & Model Targeting

Voice.ai resolves API properties inherently different from standard TTS parameters:

*   **Format Resolutions (`_FORMAT_MAP`):** Internal definitions `"linear16"` correctly route towards `"pcm_16000"` parameter arrays. Internal definitions `"opus"` target `"opus_48000_64"`. This directly influences returned `sample_rate` logic dynamically (switching from 16kHz to 48kHz automatically).
*   **Model Adjustments:** Voice.ai tracks multiple models explicitly. If `VOICEAI_TTS_MODEL` is set to `"multilingual-something"`, but the detected/passed `language` is purely `"en"`, the `_synthesize` module inherently edits the parameter dictionary replacing `.replace("multilingual-", "")` resolving natively to a faster specialized english model automatically.

---

## Configuration

### `get_voiceai_headers()` (`config.py`)

Generates strict formatting API headers.

*   Builds the JSON dict mapping: `Authorization: Bearer <API_KEY>` natively.
*   Acts as an architecture boundary triggering explicit `RuntimeError` failure on initialization if `VOICE_AI_API_KEY` isn't accessible in server scope.
