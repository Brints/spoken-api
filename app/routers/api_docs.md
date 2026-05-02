# FluentMeet Routers Documentation

> **Package Location:** `/app/routers`
> **Purpose:** Centralized API Route Aggregation

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Router Configuration](#router-configuration)
  - [Authentication Router](#authentication-router)
  - [User Router](#user-router)
  - [Meeting Router](#meeting-router)
  - [WebSocket Router](#websocket-router)
- [Integration](#integration)

---

## Overview

The `routers` package in FluentMeet is a lightweight, centralized aggregation layer. It uses FastAPI's `APIRouter.include_router()` method to collect the distinct, feature-based routers from various modules (authentication, user profile, meetings, websockets) and bundle them into a single, cohesive API router (`api_router`). 

This single `api_router` is then mounted by the main FastAPI application instance (typically in `app/main.py`), keeping the core application entry point clean and adhering to a modular, decoupled architecture.

---

## Architecture

The architecture relies on the feature packages defining their own localized routing and prefixes, which are then combined here.

```
┌────────────────────────────────────────────────────────┐
│                   app/main.py                          │
│     app.include_router(api_router, prefix="/api/v1")   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                 app/routers/api.py                     │
│                  (api_router)                          │
├──────────────┬───────────────┬─────────────────────────┤
│              │               │                         │
▼              ▼               ▼                         ▼
auth_router  users_router  meeting_router            ws_router
(no prefix)  (no prefix)   (prefix="/meetings")      (prefix="/ws")
│              │               │                         │
│              │               │                         │
app/modules/   app/modules/    app/modules/              app/modules/
auth/          user/           meeting/                  meeting/
router.py      router.py       router.py                 ws_router.py
```

*Note: Feature modules like `auth` and `user` define their own sub-prefixes internally (e.g., `prefix="/auth"` and `prefix="/users"` respectively inside their own router definitions), whereas prefixes like `/meetings` and `/ws` are explicitly assigned during inclusion in `api.py`.*

---

## Router Configuration

The `api_router` integrates the following module routers:

### Authentication Router
- **Imported from:** `app.modules.auth.router.router`
- **Prefix:** None assigned in `api.py` (Inherits `/auth` from the module itself).
- **Tags:** `auth`
- **Purpose:** Handles signup, login, password recovery, token rotation, and Google OAuth 2.0 flows.

### User Router
- **Imported from:** `app.modules.user.router.router`
- **Prefix:** None assigned in `api.py` (Inherits `/users` from the module itself).
- **Tags:** `users`
- **Purpose:** Handles authenticated user profile fetching, updating, avatar uploading, and Soft/Hard GDPR-compliant account deletion.

### Meeting Router
- **Imported from:** `app.modules.meeting.router.router`
- **Prefix:** `/meetings` (Explicitly assigned in `api.py`).
- **Tags:** `meetings`
- **Purpose:** Handles meeting room CRUD operations, configurations, waitlist lobby admission logic, and email invitations.

### WebSocket Router
- **Imported from:** `app.modules.meeting.ws_router.router`
- **Prefix:** `/ws` (Explicitly assigned in `api.py`).
- **Tags:** `websockets`
- **Purpose:** Handles persistent connections for real-time WebRTC signaling (`/signaling`), Kafka-bridged audio stream ingestion/egress (`/audio`), and translated transcription payloads (`/captions`).

---

## Integration

To integrate this bundle into the main FastAPI application, `api_router` is imported and mounted inside the app initialization logic. 

**Example (`app/main.py`):**

```python
from fastapi import FastAPI
from app.routers.api import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
)

# Mounts all collected routes under the global API prefix (e.g., /api/v1)
app.include_router(api_router, prefix=settings.API_V1_STR)
```
