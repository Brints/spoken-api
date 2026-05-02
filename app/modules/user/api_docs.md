# FluentMeet User API Documentation

> **Base URL:** `/api/v1/users`
> **Version:** 1.0 · **Protocol:** REST over HTTPS · **Content-Type:** `application/json` (except for avatar upload)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Endpoints](#endpoints)
  - [GET /me](#get-me)
  - [PATCH /me](#patch-me)
  - [POST /me/avatar](#post-meavatar)
  - [DELETE /me](#delete-me)
- [Request / Response Schemas](#request--response-schemas)
- [Internal Services](#internal-services)

---

## Overview

The FluentMeet user module manages the authenticated user's profile and account settings. It provides endpoints for:

- **Profile Retrieval:** Fetching the current user's profile details safely (excluding sensitive data like hashed passwords).
- **Profile Updates:** Modifying display name and language preferences.
- **Avatar Management:** Uploading and securely replacing profile pictures via Cloudinary.
- **Account Deletion:** GDPR-compliant account deletion (soft and hard deletes) complete with immediate session invalidation and cloud asset cleanup.

---

## Architecture

The user module leans on the central `auth.models.User` ORM model but encapsulates all business logic related to profile management in its own `UserService`.

```
┌────────────────────────────────────────────────────────┐
│                    FastAPI Router                      │
│            (app/modules/user/router.py)                │
├──────────────────────────┬─────────────────────────────┤
│                          │                             │
│       UserService        │     StorageService          │
│       (service.py)       │ (external_services/.../)    │
│                          │                             │
│            │             │              │              │
│            ▼             │              ▼              │
│     ┌────────────┐       │       ┌──────────────┐      │
│     │PostgreSQL  │       │       │  Cloudinary  │      │
│     │  (Users)   │       │       │   (Avatars)  │      │
│     └────────────┘       │       └──────────────┘      │
│                          ▼                             │
│                   ┌────────────┐                       │
│                   │   Redis    │                       │
│                   │ (Sessions) │                       │
│                   └────────────┘                       │
└────────────────────────────────────────────────────────┘
```

### Module Files

| File                       | Purpose                                                                                                                |
|----------------------------|------------------------------------------------------------------------------------------------------------------------|
| `router.py`                | FastAPI route definitions (`/me` endpoints). Handles session revocation for deletes and proxying to external services. |
| `service.py`               | DB-level CRUD operations (`UserService`), handling safe partial updates, and soft/hard deletes.                        |
| `schemas.py`               | Pydantic request/response models tailored for public profile consumption.                                              |
| `dependencies.py`          | FastAPI dependency injection factory (`get_user_service`).                                                             |
| `constants.py`             | Standardized response messages and Cloudinary folder definitions (`AVATAR_FOLDER`).                                    |
| `models.py` / `helpers.py` | Kept for module structural consistency (currently empty, relies on `app.modules.auth.models.User`).                    |

---

## Endpoints

*(All endpoints in this module implicitly require the user to be authenticated.)*

---

### GET /me

Retrieve the current authenticated user's profile.

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Response: `200 OK`**

```json
{
  "status_code": 200,
  "status": "success",
  "message": "User profile retrieved successfully.",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "Jane Doe",
    "avatar_url": "https://res.cloudinary.com/.../fluentmeet/avatars/abc.jpg",
    "speaking_language": "en",
    "listening_language": "fr",
    "is_active": true,
    "is_verified": true,
    "user_role": "user",
    "created_at": "2026-04-10T12:00:00Z"
  }
}
```

---

### PATCH /me

Update the current user's profile properties. The update payload is partial; only supplied fields are modified.

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Request Body:**

```json
{
  "full_name": "Jane H. Doe",
  "listening_language": "es"
}
```

| Field                | Type             | Required | Notes                                      |
|----------------------|------------------|----------|--------------------------------------------|
| `full_name`          | `string \| null` | ❌        | Max 255 chars                              |
| `speaking_language`  | `string (enum)`  | ❌        | Values: `en`, `fr`, `de`, `es`, `it`, `pt` |
| `listening_language` | `string (enum)`  | ❌        | Values: `en`, `fr`, `de`, `es`, `it`, `pt` |

**Response: `200 OK`**
Returns a `ProfileApiResponse` enclosing the updated `UserProfileResponse`.

---

### POST /me/avatar

Upload or replace the user's profile avatar. Files are stored and transformed heavily via Cloudinary (cropped to face).

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Content-Type:** `multipart/form-data`

**Request Body:**

| Form Field | Type   | Required | Notes                                                  |
|------------|--------|----------|--------------------------------------------------------|
| `avatar`   | `File` | ✅        | Valid formats: JPEG, PNG, WebP. Max upload size: 5 MB. |

**Behavior:**
1. If the user already has an avatar URL matching the host `AVATAR_FOLDER`, the server calculates the old `public_id` and explicitly hard-deletes the old asset from Cloudinary via `StorageService`.
2. The server issues a secure upload request parsing the uploaded file buffer to Cloudinary, forcing a synchronous face-cropping logic transform (`width=400, height=400, crop=fill, gravity=face`).
3. Overwrites the User `avatar_url` database string with the fresh `secure_url`.

**Response: `200 OK`**
Returns an `AvatarUploadResponse` containing the full updated public user data.

---

### DELETE /me

Delete the authenticated user's account and instantly invalidate all sessions.

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Query Parameters:**

| Parameter | Type      | Required | Description                                                                                                     |
|-----------|-----------|----------|-----------------------------------------------------------------------------------------------------------------|
| `hard`    | `boolean` | ❌        | Default: `false`. Standard request triggers a soft delete. Passing `?hard=true` triggers a permanent hard wipe. |

**Behavior (Soft Delete - Default):**
- Modifies DB setting `deleted_at = NOW()` and `is_active = False`. The database row and connected relations are retained for recovery or auditing.

**Behavior (Hard Delete - `?hard=true`):**
- Triggers GDPR-compliant total erasure.
- Parses the active Cloudinary `avatar_url` (if any), identifies the `public_id` and permanently deletes the remote image.
- Permanently deletes all `VerificationToken` rows bound to the user.
- Permanently hard-deletes the `User` database row itself.

**Post-Delete Session Teardown (Triggered in both modes):**
1. **Redis Blacklist:** Evaluates the `jti` of the actively submitted `Bearer` token and blacklists the identifier natively inside the Token Store limiting its remaining lifetime to zero.
2. **Redis Revocation:** Scans for and wipes **all** currently valid Refresh Tokens tied to the user email.
3. **Cookie Ejection:** Attaches `Set-Cookie` directives setting the HTTP-only `refresh_token` value to nothing, essentially wiping it from the connected client browser.

**Response: `200 OK`**
```json
{
  "status": "ok",
  "message": "Account has been deactivated and scheduled for deletion."
}
```

---

## Request / Response Schemas

### UserProfileResponse

The primary sanitized entity containing the public footprint of an authenticated user. It strictly omits relational or highly sensitive fields (`hashed_password`, `deleted_at`, `updated_at`).

| Field                | Type             | Description                                      |
|----------------------|------------------|--------------------------------------------------|
| `id`                 | `UUID`           | Unique account string.                           |
| `email`              | `string`         | Normalized e-mail.                               |
| `full_name`          | `string \| null` |                                                  |
| `avatar_url`         | `string \| null` | FQDN Cloudinary link.                            |
| `speaking_language`  | `string (enum)`  |                                                  |
| `listening_language` | `string (enum)`  |                                                  |
| `is_active`          | `bool`           | Default `true`.                                  |
| `is_verified`        | `bool`           | True if user resolved their email verify prompt. |
| `user_role`          | `string`         | Default `user`.                                  |
| `created_at`         | `datetime`       |                                                  |

### Envelopes

Endpoints consistently envelope success data inside the following wrappers:
- `ProfileApiResponse` -> `{ status_code, status, message, data: UserProfileResponse }`
- `AvatarUploadResponse` -> `{ status_code, status, message, data: UserProfileResponse }`
- `DeleteResponse` -> `{ status, message }`

---

## Internal Services

### UserService (`service.py`)

A decoupled database manipulation layer interacting safely with the central `User` ORM Model.

| Method                           | Purpose                                                                                                                |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------|
| `get_user_by_id(user_id)`        | Single record entity load.                                                                                             |
| `update_user(user, update_data)` | Safely runs simple setter injections checking against null validations.                                                |
| `update_avatar_url(user, url)`   | Targeted atomical avatar set.                                                                                          |
| `soft_delete_user(user)`         | Performs column mutations locking `deleted_at` & `is_active`.                                                          |
| `hard_delete_user(user)`         | Runs heavy cascading hard deletion sequences targeting `VerificationToken`s before destroying the base SQL entity row. |

### Helper Methods (`router.py`)

| Method                           | Purpose                                                                                                                                                                                                 |
|----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `_extract_public_id(secure_url)` | String modification utility used exclusively to derive the inner specific `public_id` string from an outbound Cloudinary URL required to command external delete actions over its API.                  |
| `_extract_bearer_token(request)` | Bypasses standard FastAPI dependency logic to manually intercept the raw Bearer JWT text off the live request, necessary for JTI calculation & token string Blacklisting during account deletion steps. |
