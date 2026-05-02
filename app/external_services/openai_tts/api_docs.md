# FluentMeet OpenAI TTS Documentation

> **Package Location:** `/app/external_services/openai_tts`
> **Purpose:** Handles external asynchronous integrations with the OpenAI Text-to-Speech API.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Public API (`service.py`)](#public-api-servicepy)
- [Audio Formats](#audio-formats)
- [Configuration](#configuration)

---

## Overview

The `app/external_services/openai_tts` package acts as the active backend for stage 4 of the real-time audio pipeline. It intercepts translated text streams and synthesizes them into dynamic real-time human voices using OpenAI's `tts-1` model via the `/v1/audio/speech` endpoints.

---

## Architecture

To minimize dependencies and footprint, avoiding heavy pip installments, the `OpenAITTSService` entirely abstracts OpenAI SDK endpoints via raw `httpx.AsyncClient` objects natively. 

It is designed as a pure stateless singleton and gets injected dynamically into the `TTSWorker` Daemon inside `app/services` based on the `.env` file configuration setting dictating whether Voice.ai or OpenAI drives speech synthesis.

---

## Public API (`service.py`)

### `OpenAITTSService` 

The fully asynchronous service layer encapsulating synthesis logic.

#### `synthesize(text, voice, encoding)`
Executes the API request to retrieve the generated Audio chunk.
*   **Args:**
    *   `text` *(str)*: Target string text block to convert to voice.
    *   `voice` *(str, optional)*: OpenAI voice profile (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`). Overrides environmental mapping defaults.
    *   `encoding` *(str)*: Required chunk encoding mapping (`"linear16"` or `"opus"`).
*   **Returns:**
    Returns a strict `dict` containing the binary footprint needed to transmit over Kafka natively.
    ```json
    {
      "audio_bytes": "base64-encoded-audio-bytes",
      "sample_rate": 24000,
      "latency_ms": 284.1
    }
    ```
*   **Exception Behavior:** Immediately raises `httpx.HTTPStatusError` on non-200 responses to enforce the system-wide Dead Letter Queue routing schema via the `Exceptions` trapping mechanism.

---

## Audio Formats

Native AI API endpoints refer to raw data by highly specific designations natively (e.g. `pcm` instead of `linear16`). The underlying module natively provides the dictionary `_FORMAT_MAP` routing internal definitions like `"linear16"` directly to `"pcm"` in the OpenAPI REST schemas. 

*Note: OpenAI inherently resolves standard `pcm` packets natively to a 24kHZ mono output footprint, distinct from STT endpoints expecting 16kHz standard configurations.*

---

## Configuration

### `get_openai_tts_headers()` (`config.py`)

Generates strict formatting API headers.

*   Builds the JSON dict mapping: `Authorization: Bearer <API_KEY>` natively.
*   Enforces `Content-Type: application/json`.
*   Acts as an architecture boundary: automatically throws a `RuntimeError` failure natively on instantiation if the application failed to boot with `OPENAI_API_KEY` defined.
