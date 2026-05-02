# FluentMeet Core Application Documentation

> **Package Location:** `/app/core`
> **Purpose:** Houses all fundamental components that are globally shared across the entire application ecosystem, strictly agnostic of specific application models.

---

## Table of Contents

- [Overview](#overview)
- [Configuration (`config.py`)](#configuration-configpy)
- [Security (`security.py`)](#security-securitypy)
- [Exception Handlers & Responses](#exception-handlers--responses)
- [System Dependencies (`dependencies.py`)](#system-dependencies-dependenciespy)
- [Sanitization (`sanitize.py`)](#sanitization-sanitizepy)

---

## Overview

The `app/core` package serves as the backbone of the application. It bootstraps application config configurations asynchronously, intercepts exceptions homogeneously, drives system security schemas securely, and houses FastApi `Depends()` routines globally to evade circular imports. 

---

## Configuration (`config.py`)

Leverages `pydantic_settings`.

### The `Settings` Object
*   Extracts natively parameters stored inside `./.env` matching dynamically against types automatically parsing logic.
*   Resolves variables for Database URls, JWT Secrets, Redis caches, Kafka bootstrap brokers explicitly, and cloud provider APIs like OpenAI / DL environments seamlessly.
*   Forces dynamic fallback loading the PyProject version using `tomllib`.

---

## Security (`security.py`)

Handles cryptographic payload verification schemas explicitly without accessing Database constructs seamlessly.

*   **Bcrypt Password Context:** `hash_password()` and `verify_password()`.
    *   Implements a native exception wrapper patching standard deprecated `passlib` behaviors failing aggressively on unmanaged `bcrypt 4.0.0+` versions transparently overriding bounds dynamically.
*   **JWT Creation (`encode`):**
    *   `create_access_token()`: Returns a short-lived token using explicit TTL mappings native to configuration structures (expiring natively in ~60mins). 
    *   `create_refresh_token()`: Returns a long-lived tuple returning the securely allocated JTI identifier logic explicitly mappings directly against settings (e.g., 7 days).

---

## Exception Handlers & Responses

### Responses (`error_responses.py`)
Standardizes REST API outputs homogenously guaranteeing frontend UI frameworks never fail parsing generic trace responses gracefully.

*   `ErrorDetail`: Nested lists explicitly tracking localized parameter validation triggers dynamically.
*   `ErrorResponse`: Unifies status, descriptor `code`, human-readable `message` securely.

### Handlers (`exception_handlers.py`)
Registered on core startup logic intercepting framework exceptions dynamically.

*   Converts Starlette/FastAPI `RequestValidationError` cleanly into `400` validation constraints structures.
*   Binds generic unhandled HTTP 500 stacks dynamically dumping details efficiently via `sanitize_for_log()`.

### Custom Error Framework (`exceptions.py`)
Developers natively invoke `raise BadRequestException("Missing ID")` mapping gracefully dynamically down to HTTP structures utilizing the Handlers. Allows custom error codes defined seamlessly (e.g. `code="INVALID_OTP"` natively mapped).

---

## System Dependencies (`dependencies.py`)

Decouples authentication blocks natively allowing models mapping efficiently natively circumventing explicit Circular dependencies seamlessly.

Provides FastApi injectable logic defining explicit Token/Bearer evaluations transparently parsing JWT variables gracefully extracting explicit target entities locally from the Database dynamically checking `is_active` flags before propagating securely to Endpoint Routers automatically.

---

## Sanitization (`sanitize.py`)

Intercepts log mechanisms aggressively globally preventing explicit log-spoofing injection vectors smoothly intercepting inputs wrapping string payloads automatically truncating heavy lengths tracking string components securely natively tracking unmanaged inputs across routes dynamically.
