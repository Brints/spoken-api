# FluentMeet DeepL & LLM Translation Documentation

> **Package Location:** `/app/external_services/deepl`
> **Purpose:** Handles external asynchronous integrations with the DeepL `/v2/translate` API and provides OpenAI LLM algorithmic fallbacks.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Public API (`service.py`)](#public-api-servicepy)
- [Fallback Mechanisms](#fallback-mechanisms)
- [Language Code Mapping](#language-code-mapping)

---

## Overview

The `app/external_services/deepl` package acts as the active backend for stage 3 of the real-time audio pipeline. It intercepts STT transcriptions and converts them dynamically into alternate target languages required by individual users in the meeting lobby.

---

## Architecture

To remain fully stateless and incredibly lightweight without depending on strict external third-party SDKs, the translation engines fire purely via `httpx.AsyncClient` objects wrapping the provider APIs.

### Services Exposed
1. **`DeepLTranslationService`**: The primary translation engine pointing at `api-free.deepl.com`.
2. **`OpenAITranslationFallback`**: A secondary translation engine pivoting to `gpt-4o-mini` capable of interpreting unsupported dialects or surviving a DeepL service outage.

These are injected globally by the `TranslationWorker` daemon in the `app/services` directory.

---

## Public API (`service.py`)

Both Translation services export an identical `translate()` asynchronous signature allowing polymorphing swapping on error conditions.

#### `translate(text, source_language, target_language)`
*   **Args:**
    *   `text` *(str)*: The text buffer requiring translation.
    *   `source_language` *(str)*: ISO 639-1 Hint language tag (e.g., `"fr"`).
    *   `target_language` *(str)*: Target localized ISO 639-1 tag constraint.
*   **Returns:**
    Returns a unified `dict` payload structure standard against multiple engines:
    ```json
    {
      "translated_text": "Bonjour le monde",
      "latency_ms": 115.5
    }
    ```
*   **Exception Behavior:** Both primary engines explicitly raise `httpx.HTTPStatusError` aggressively so the `TranslationWorker` pipeline code can manage failures explicitly or execute immediate fallbacks.

---

## Fallback Mechanisms

DeepL is phenomenally fast, but supports a relatively narrow funnel of active language mappings. 

Inside the logic, before spinning up an HTTP context, the DeepL mapping is checked via `supports_language()`. If this yields `False`, or if a 500 API exception cascades back from DeepL, the system instantly catches the logic and bounces the payload securely to `OpenAITranslationFallback`.

The fallback prompts OpenAI using a zero-shot strictly confined chat string: `"You are a professional translator. Translate the following text from {source} to {target}. Return ONLY the translated text, nothing else."`

---

## Language Code Mapping

DeepL requires esoteric capitalization modifications (e.g. `EN-US` instead of `en`, `PT-BR` instead of `pt`) which breaks pipeline standards. 

The service defines a private internal mapping table `_DEEPL_LANG_MAP` that captures the front-end user `en`, `de`, `fr` lowercase configurations and dynamically adapts them to DeepL formatting on ingress, reverting answers gracefully back natively before the function returns them up into Kafka topics.
