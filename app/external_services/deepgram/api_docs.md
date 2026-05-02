# FluentMeet Deepgram Integration Documentation

> **Package Location:** `/app/external_services/deepgram`
> **Purpose:** Handles external asynchronous integrations with the Deepgram Speech-to-Text API.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Public API](#public-api)
- [Configuration](#configuration)

---

## Overview

The `app/external_services/deepgram` package wraps the Deepgram REST `/v1/listen` endpoint natively enabling extremely fast conversion of `bytes` objects into text Strings. 

It is designed to be fully stateless and heavily depends on FastAPI standard dependencies & `httpx.AsyncClient` objects rather than installing Deepgram's heavy Python SDK, preserving application footprint and avoiding dependency bloat.

---

## Architecture

This package exposes a single class `DeepgramSTTService` bound as a Singleton.
It is actively injected and utilized globally by the `STTWorker` consumer daemon listening to Kafka `audio.raw`.

### Execution Flow
1. Receives base64-decoded PCM strings.
2. Injects required API metadata mapping to settings boundaries.
3. Fires the `POST` request out asynchronously to the web REST Endpoint returning results.

---

## Public API

### `DeepgramSTTService` (`service.py`)

A fully typed async service wrapping the REST endpoint.

#### `transcribe(audio_bytes, language, sample_rate, encoding)`
Sends a block of data to Deepgram to fetch an interpretation.
*   **Args:**
    *   `audio_bytes` *(bytes)*: Standard PCM binary string or OPUS stream bytes.
    *   `language` *(str)*: A localized ISO 639-1 code hint (e.g., `"en"`).
    *   `sample_rate` *(int)*: Standard `16000` (Hz).
    *   `encoding` *(str)*: Tells Deepgram the format (`"linear16"` or `"opus"`).
*   **Returns:**
    Returns a unified `dict` payload structure standard against multiple engines:
    ```json
    {
      "text": "Hello world",
      "confidence": 0.99,
      "detected_language": "en",
      "latency_ms": 32.5
    }
    ```
*   **Exception Behavior:** Raises `httpx.HTTPStatusError` aggressively when anything other than an HTTP 2xx code is returned to enforce fallback failure and Dead-Letter-Queue routing in the caller blocks.

---

## Configuration

### `get_deepgram_headers()` (`config.py`)

Ensures the authentication mechanisms are mapped securely from environment definitions.

*   Builds the dict mapping `Authorization: Token <API_KEY>`
*   Fails fast natively issuing `RuntimeError` on startup if `DEEPGRAM_API_KEY` is completely missing from `.env` or Server Environment.
