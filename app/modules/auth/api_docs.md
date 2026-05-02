# FluentMeet Authentication API Documentation

> **Base URL:** `/api/v1/auth`
> **Version:** 1.0 · **Protocol:** REST over HTTPS · **Content-Type:** `application/json`

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Authentication Flow](#authentication-flow)
- [Security Mechanisms](#security-mechanisms)
- [Endpoints](#endpoints)
  - [POST /signup](#post-signup)
  - [POST /login](#post-login)
  - [GET /verify-email](#get-verify-email)
  - [POST /resend-verification](#post-resend-verification)
  - [POST /forgot-password](#post-forgot-password)
  - [POST /reset-password](#post-reset-password)
  - [POST /change-password](#post-change-password)
  - [POST /logout](#post-logout)
  - [POST /refresh-token](#post-refresh-token)
  - [GET /google/login](#get-googlelogin)
  - [GET /google/callback](#get-googlecallback)
- [Data Models](#data-models)
- [Request / Response Schemas](#request--response-schemas)
- [Error Codes Reference](#error-codes-reference)
- [Configuration Reference](#configuration-reference)
- [Internal Services](#internal-services)

---

## Overview

The FluentMeet authentication module provides a complete identity and access management system built on **FastAPI**. It supports:

- **Email/password registration** with mandatory email verification.
- **Google OAuth 2.0** social login with automatic account linking.
- **JWT-based session management** using short-lived access tokens and long-lived refresh tokens.
- **Refresh Token Rotation** with automatic reuse detection and full session invalidation.
- **Account lockout** after repeated failed login attempts.
- **Password recovery** via secure one-time email tokens.
- **Rate limiting** on all sensitive endpoints via SlowAPI.

All tokens are signed with **HS256** and a server-side secret key. Refresh tokens are delivered exclusively via **HttpOnly, Secure, SameSite=Strict** cookies and are never exposed in response bodies.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI Router                          │
│                      (app/modules/auth/router.py)               │
├──────────┬──────────┬──────────────┬───────────┬────────────────┤
│          │          │              │           │                │
│  AuthService   AuthVerification  GoogleOAuth  AccountLockout   │
│  (service.py)  Service           Service      Service          │
│          │   (verification.py) (oauth_google.py) (account_     │
│          │          │              │           │  lockout.py)   │
│          ▼          ▼              │           ▼                │
│    ┌──────────┐  ┌──────────┐     │    ┌────────────┐          │
│    │ Security │  │  Email   │     │    │   Redis    │          │
│    │ Service  │  │ Producer │     │    │ (lockout + │          │
│    │(core/    │  │ Service  │     │    │  tokens)   │          │
│    │security) │  │          │     │    └────────────┘          │
│    └──────────┘  └──────────┘     │                            │
│          │                        │                            │
│          ▼                        │                            │
│    ┌──────────┐            ┌──────────┐   ┌────────────┐       │
│    │PostgreSQL│            │ Google   │   │ TokenStore │       │
│    │ (Users,  │            │ OAuth2   │   │  Service   │       │
│    │ Tokens)  │            │ Provider │   │(token_     │       │
│    └──────────┘            └──────────┘   │ store.py)  │       │
│                                           └────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### Module Files

| File                 | Purpose                                                                           |
|----------------------|-----------------------------------------------------------------------------------|
| `router.py`          | FastAPI route definitions and HTTP-layer logic                                    |
| `service.py`         | Core business logic — signup, login, password reset/change, OAuth user resolution |
| `schemas.py`         | Pydantic request/response models and validators                                   |
| `models.py`          | SQLAlchemy ORM models (`User`, `VerificationToken`, `PasswordResetToken`)         |
| `dependencies.py`    | FastAPI dependency injection factories                                            |
| `verification.py`    | Email verification token lifecycle                                                |
| `token_store.py`     | Redis-backed refresh token storage and access token blacklisting                  |
| `account_lockout.py` | Redis-backed brute-force protection                                               |
| `oauth_google.py`    | Google OAuth 2.0 authorization code flow                                          |
| `constants.py`       | Enums — `UserRole`, `SupportedLanguage`                                           |

---

## Authentication Flow

### Email/Password Registration Flow

```
Client                          Server                        Email Service
  │                               │                               │
  │  POST /signup                 │                               │
  │  {email, password, ...}  ──►  │                               │
  │                               │── Create user (unverified)    │
  │                               │── Generate verification token │
  │                               │── Enqueue verification email ──►
  │  ◄── 201 {user_id, ...}      │                               │
  │                               │                               │
  │  User clicks email link       │                               │
  │  GET /verify-email?token=...──►│                               │
  │                               │── Validate token              │
  │                               │── Set is_verified = True      │
  │  ◄── 200 {message}           │                               │
  │                               │                               │
  │  POST /login                  │                               │
  │  {email, password}       ──►  │                               │
  │                               │── Verify credentials          │
  │                               │── Check lockout / verified    │
  │                               │── Issue AT + RT               │
  │  ◄── 200 {access_token}      │                               │
  │  ◄── Set-Cookie: refresh_token│                               │
```

### Token Refresh Flow (Rotation)

```
Client                          Server                     Redis
  │                               │                          │
  │  POST /refresh-token          │                          │
  │  Cookie: refresh_token=...──► │                          │
  │                               │── Decode RT JWT          │
  │                               │── Check JTI valid? ─────► │
  │                               │  ◄── Yes                 │
  │                               │── Revoke old JTI ───────► │
  │                               │── Save new JTI ─────────► │
  │  ◄── 200 {new_access_token}  │                          │
  │  ◄── Set-Cookie: new_rt      │                          │
  │                               │                          │
  │  ⚠️ Reuse of OLD RT         │                          │
  │  POST /refresh-token          │                          │
  │  Cookie: old_rt          ──►  │                          │
  │                               │── Check JTI valid? ─────► │
  │                               │  ◄── No (revoked!)       │
  │                               │── REVOKE ALL sessions ──► │
  │  ◄── 401 REFRESH_TOKEN_REUSE │                          │
```

### Google OAuth 2.0 Flow

```
Client                          Server                     Google
  │                               │                          │
  │  GET /google/login       ──►  │                          │
  │                               │── Generate state token   │
  │                               │── Store in Redis (10min) │
  │  ◄── 302 → Google consent     │                          │
  │                               │                          │
  │  (user authenticates with Google)                        │
  │                               │                          │
  │  GET /google/callback         │                          │
  │  ?code=...&state=...     ──►  │                          │
  │                               │── Verify state from Redis│
  │                               │── Exchange code ─────────►│
  │                               │  ◄── access_token        │
  │                               │── Get user info ─────────►│
  │                               │  ◄── {email, name, ...}  │
  │                               │── Find or create user    │
  │                               │── Issue AT + RT          │
  │  ◄── 302 → frontend#access_token=...                    │
  │  ◄── Set-Cookie: refresh_token│                          │
```

---

## Security Mechanisms

### JWT Token Strategy

| Token             | Delivery        | Lifetime              | Claims                                         | Storage                            |
|-------------------|-----------------|-----------------------|------------------------------------------------|------------------------------------|
| **Access Token**  | Response body   | 60 min (configurable) | `sub` (email), `jti`, `exp`, `type: "access"`  | Client-side (memory/localStorage)  |
| **Refresh Token** | HttpOnly cookie | 7 days (configurable) | `sub` (email), `jti`, `exp`, `type: "refresh"` | Redis (server-side JTI validation) |

- **Algorithm:** HS256
- **Library:** python-jose
- **Signing Key:** `SECRET_KEY` from environment

### Refresh Token Rotation

Every token refresh issues a **new** refresh token and revokes the old one. If a revoked token is reused (indicating possible theft), **all sessions for that user are immediately invalidated**.

### Access Token Blacklisting

On logout, the access token's JTI is added to a Redis blacklist with a TTL matching the token's remaining lifetime. The `get_current_user` dependency checks this blacklist on every authenticated request.

### Account Lockout Policy

| Parameter                   | Default | Description                         |
|-----------------------------|---------|-------------------------------------|
| `MAX_FAILED_LOGIN_ATTEMPTS` | 5       | Consecutive failures before lockout |
| `ACCOUNT_LOCKOUT_DAYS`      | 5       | Duration of the lockout period      |

**Redis Keys:**
- `login_attempts:{email}` — integer counter, no TTL (cleared on success)
- `account_locked:{email}` — flag (`"1"`), TTL = lockout period

A successful login resets the failure counter.

### Password Hashing

- **Primary:** passlib with bcrypt scheme
- **Fallback:** raw bcrypt (for compatibility with newer bcrypt builds)
- Auto-deprecated hash schemes are upgraded on verification.

### Rate Limiting

All sensitive endpoints are rate-limited using **SlowAPI** (based on client IP):

| Endpoint                    | Limit     |
|-----------------------------|-----------|
| `POST /login`               | 10/minute |
| `POST /resend-verification` | 3/minute  |
| `POST /forgot-password`     | 5/minute  |
| `POST /reset-password`      | 5/minute  |
| `POST /change-password`     | 10/minute |
| `POST /logout`              | 20/minute |
| `POST /refresh-token`       | 30/minute |

### Cookie Security

All refresh token cookies are set with:

```
HttpOnly:  true       (no JavaScript access)
Secure:    true       (HTTPS only)
SameSite:  strict     (no cross-site requests)
Path:      /api/v1/auth
Max-Age:   <refresh_token_ttl>
```

---

## Endpoints

---

### POST /signup

Register a new user account. A verification email is sent asynchronously.

**Request Body:**

```json
{
  "email": "user@example.com",
  "password": "securePass123",
  "confirm_password": "securePass123",
  "full_name": "Jane Doe",
  "speaking_language": "en",
  "listening_language": "fr",
  "accepted_terms": true
}
```

| Field                | Type             | Required | Constraints                                                           |
|----------------------|------------------|----------|-----------------------------------------------------------------------|
| `email`              | `string (email)` | ✅        | Valid email, auto-lowercased                                          |
| `password`           | `string`         | ✅        | Minimum 8 characters                                                  |
| `confirm_password`   | `string`         | ✅        | Must match password exactly                                           |
| `accepted_terms`     | `boolean`        | ✅        | Must be `true` — user must accept Terms of Service and Privacy Policy |
| `full_name`          | `string \| null` | ❌        | Max 255 chars, auto-trimmed                                           |
| `speaking_language`  | `enum`           | ❌        | Default: `"en"`. Values: `en`, `fr`, `de`, `es`, `it`, `pt`           |
| `listening_language` | `enum`           | ❌        | Default: `"en"`. Values: `en`, `fr`, `de`, `es`, `it`, `pt`           |

**Response: `201 Created`**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "full_name": "Jane Doe",
  "speaking_language": "en",
  "listening_language": "fr",
  "user_role": "user",
  "is_active": true,
  "is_verified": false,
  "created_at": "2026-04-10T12:00:00Z"
}
```

**Error Responses:**

| Status | Code                       | Condition                                                   |
|--------|----------------------------|-------------------------------------------------------------|
| `409`  | `EMAIL_ALREADY_REGISTERED` | An account with this email already exists                   |
| `422`  | —                          | Validation error (missing fields, passwords mismatch, etc.) |

**Side Effects:**
- Creates an unverified `User` record in PostgreSQL.
- Generates a `VerificationToken` (UUID, 24h expiry).
- Enqueues a verification email via Kafka (non-blocking; signup succeeds even if email dispatch fails).

---

### POST /login

Authenticate a registered user with email and password.

**Request Body:**

```json
{
  "email": "user@example.com",
  "password": "securePass123"
}
```

| Field      | Type             | Required |
|------------|------------------|----------|
| `email`    | `string (email)` | ✅        |
| `password` | `string`         | ✅        |

**Response: `200 OK`**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Response Headers:**

```
Set-Cookie: refresh_token=eyJ...; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=604800
```

**Error Responses:**

| Status | Code                  | Condition                                                                              |
|--------|-----------------------|----------------------------------------------------------------------------------------|
| `400`  | `MISSING_CREDENTIALS` | Empty request body                                                                     |
| `401`  | `INVALID_CREDENTIALS` | Wrong email or password *(Returns `details: [{"attempts_remaining": N}]`)*             |
| `403`  | `EMAIL_NOT_VERIFIED`  | Account exists but email is not verified                                               |
| `403`  | `ACCOUNT_DELETED`     | Account has been soft-deleted                                                          |
| `403`  | `ACCOUNT_LOCKED`      | Too many failed login attempts *(Returns `details: [{"lock_time_left": "duration"}]`)* |

**Example Response (Invalid Credentials):**

```json
{
  "status": "error",
  "code": "INVALID_CREDENTIALS",
  "message": "Invalid email or password.",
  "details": [
    {
      "attempts_remaining": 4
    }
  ]
}
```

**Example Response (Account Locked):**

```json
{
    "status": "error",
    "code": "ACCOUNT_LOCKED",
    "message": "Account is temporarily locked due to too many failed login attempts.",
    "details": [
        {
            "lock_time_left": "4 days, 23 hours and 29 minutes"
        }
    ]
}
```

**Rate Limit:** 10 requests/minute per IP.

**Security Behavior:**
- Failed attempts increment the lockout counter (even for non-existent emails, to prevent timing attacks).
- After 5 consecutive failures → account locked for 5 days.
- Successful login resets the failure counter.

---

### GET /verify-email

Verify a user's email address using a token from the verification email.

**Query Parameters:**

| Parameter | Type            | Required |
|-----------|-----------------|----------|
| `token`   | `string (UUID)` | ✅        |

**Example:** `GET /api/v1/auth/verify-email?token=550e8400-e29b-41d4-a716-446655440000`

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "Email successfully verified. You can now log in."
}
```

**Error Responses:**

| Status | Code            | Condition                                   |
|--------|-----------------|---------------------------------------------|
| `400`  | `MISSING_TOKEN` | No `token` query parameter provided         |
| `400`  | `INVALID_TOKEN` | Token is not a valid UUID or does not exist |
| `400`  | `TOKEN_EXPIRED` | Token has expired (default: 24 hours)       |

**Side Effects:**
- Sets `user.is_verified = True`.
- Deletes the consumed `VerificationToken`.

---

### POST /resend-verification

Request a new verification email. Always returns a generic success message to prevent **user enumeration**.

**Request Body:**

```json
{
  "email": "user@example.com"
}
```

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "If an account with that email exists, we have sent a verification email."
}
```

**Rate Limit:** 3 requests/minute per IP.

**Behavior:**
- If the user does not exist or is already verified, the endpoint silently returns success.
- Existing unexpired verification tokens for the user are deleted before issuing a new one.

---

### POST /forgot-password

Request a password reset email. Always returns a generic success message to prevent **user enumeration**.

**Request Body:**

```json
{
  "email": "user@example.com"
}
```

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "If an account with this email exists, a password reset link has been sent."
}
```

**Rate Limit:** 5 requests/minute per IP.

**Behavior:**
- Silently returns success if user does not exist, is inactive, deleted, or unverified.
- Deletes any existing `PasswordResetToken` records for the user before creating a new one.
- Token expiry: configurable via `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` (default: 60 min).
- Sends a `password_reset` email template via Kafka (non-blocking).

---

### POST /reset-password

Reset the user's password using a one-time token received via email.

**Request Body:**

```json
{
  "token": "550e8400-e29b-41d4-a716-446655440000",
  "new_password": "newSecurePass456"
}
```

| Field          | Type     | Required | Constraints          |
|----------------|----------|----------|----------------------|
| `token`        | `string` | ✅        | Non-empty            |
| `new_password` | `string` | ✅        | Minimum 8 characters |

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "Password has been reset successfully. Please log in with your new password."
}
```

**Error Responses:**

| Status | Code                  | Condition                                   |
|--------|-----------------------|---------------------------------------------|
| `400`  | `INVALID_RESET_TOKEN` | Token does not exist or user not found      |
| `400`  | `RESET_TOKEN_EXPIRED` | Token has expired                           |
| `400`  | `SAME_PASSWORD`       | New password is the same as the current one |

**Rate Limit:** 5 requests/minute per IP.

**Side Effects:**
- Updates `user.hashed_password` and `user.updated_at`.
- Deletes the consumed `PasswordResetToken`.
- Revokes **all** active refresh tokens for the user (forces re-login on all devices).
- Sends a `password_changed` security notification email.

---

### POST /change-password

Change the password for the currently authenticated user.

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Request Body:**

```json
{
  "current_password": "oldPass123",
  "new_password": "newSecurePass456"
}
```

| Field              | Type     | Required | Constraints          |
|--------------------|----------|----------|----------------------|
| `current_password` | `string` | ✅        | —                    |
| `new_password`     | `string` | ✅        | Minimum 8 characters |

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "Password updated successfully."
}
```

**Error Responses:**

| Status | Code                                    | Condition                       |
|--------|-----------------------------------------|---------------------------------|
| `400`  | `INCORRECT_PASSWORD`                    | Current password does not match |
| `400`  | `SAME_PASSWORD`                         | New password is same as current |
| `401`  | `MISSING_TOKEN` / `INVALID_CREDENTIALS` | Not authenticated               |

**Rate Limit:** 10 requests/minute per IP.

**Side Effects:**
- Updates `user.hashed_password` and `user.updated_at`.
- Revokes **all** active refresh tokens (forces re-login on all devices).
- Sends a `password_changed` security notification email.

---

### POST /logout

Log out the current session by invalidating both the access and refresh tokens.

**🔒 Requires Authentication:** `Authorization: Bearer <access_token>`

**Request:** No body required. Refresh token is read from the `refresh_token` cookie.

**Response: `200 OK`**

```json
{
  "status": "ok",
  "message": "Successfully logged out."
}
```

**Response Headers:**

```
Set-Cookie: refresh_token=; Path=/api/v1/auth; Max-Age=0   (cookie cleared)
```

**Error Responses:**

| Status | Code                                    | Condition         |
|--------|-----------------------------------------|-------------------|
| `401`  | `MISSING_TOKEN` / `INVALID_CREDENTIALS` | Not authenticated |

**Rate Limit:** 20 requests/minute per IP.

**Behavior:**
- Blacklists the access token JTI in Redis for its remaining TTL.
- Revokes the refresh token JTI (if the cookie is present).
- Clears the `refresh_token` HttpOnly cookie.

---

### POST /refresh-token

Rotate the refresh token and issue a new access token. Implements the **Refresh Token Rotation** pattern.

**Request:** No body required. The refresh token is read from the `refresh_token` HttpOnly cookie.

**Response: `200 OK`**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

**Response Headers:**

```
Set-Cookie: refresh_token=<new_rt>; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=604800
```

**Error Responses:**

| Status | Code                    | Condition                                               |
|--------|-------------------------|---------------------------------------------------------|
| `401`  | `MISSING_REFRESH_TOKEN` | No `refresh_token` cookie present                       |
| `401`  | `INVALID_REFRESH_TOKEN` | Token is expired, malformed, or not a refresh token     |
| `401`  | `REFRESH_TOKEN_REUSE`   | Revoked token was reused — **all** sessions invalidated |
| `403`  | `ACCOUNT_DEACTIVATED`   | User account has been deactivated or deleted            |

**Rate Limit:** 30 requests/minute per IP.

**Security Behavior:**
- The old refresh token JTI is revoked before the new one is saved.
- If a previously revoked JTI is used again (reuse attack), **all** refresh tokens for the user are purged from Redis and a warning is logged.

---

### GET /google/login

Initiate the Google OAuth 2.0 authorization flow by redirecting the user to Google's consent screen.

**Response: `302 Found`**

Redirects to Google's OAuth consent URL with:
- `client_id`, `redirect_uri`, `scope: "openid email profile"`
- A cryptographically random `state` parameter stored in Redis for 10 minutes.

---

### GET /google/callback

Handle the callback from Google after user authentication. This endpoint is called by Google, not by the client directly.

**Query Parameters:**

| Parameter | Type     | Required |
|-----------|----------|----------|
| `code`    | `string` | ✅        |
| `state`   | `string` | ✅        |

**Response: `302 Found`**

Redirects to: `{FRONTEND_BASE_URL}#access_token=<jwt>`

**Response Headers:**

```
Set-Cookie: refresh_token=<rt>; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Max-Age=604800
```

**Error Responses:**

| Status | Code                    | Condition                                |
|--------|-------------------------|------------------------------------------|
| `400`  | `INVALID_OAUTH_STATE`   | State token is invalid or expired        |
| `400`  | `INVALID_OAUTH_PROFILE` | Google account does not provide an email |
| `403`  | `ACCOUNT_LOCKED`        | Account is locked due to failed attempts |
| `403`  | `ACCOUNT_DEACTIVATED`   | Account is deactivated or deleted        |
| `502`  | `OAUTH_PROVIDER_ERROR`  | Failed to communicate with Google        |

**User Resolution Logic:**
1. If a user with the email exists:
   - Links the Google ID if not already linked.
   - Sets avatar URL if missing.
   - Auto-verifies the email if not already verified.
2. If no user exists:
   - Creates a new verified user with a random hashed password.
   - Sets `google_id`, `full_name`, and `avatar_url` from the Google profile.

---

## Data Models

### User

| Column               | Type           | Constraints                  | Description                  |
|----------------------|----------------|------------------------------|------------------------------|
| `id`                 | `UUID`         | PK, indexed                  | Unique user identifier       |
| `email`              | `String(255)`  | Unique, indexed, not null    | Normalized to lowercase      |
| `hashed_password`    | `String(255)`  | Not null                     | bcrypt hash                  |
| `full_name`          | `String(255)`  | Nullable                     | Display name                 |
| `is_active`          | `Boolean`      | Default: `True`              | Account active flag          |
| `is_verified`        | `Boolean`      | Default: `False`             | Email verified flag          |
| `created_at`         | `DateTime(tz)` | Default: `utc_now`           | Account creation timestamp   |
| `updated_at`         | `DateTime(tz)` | Default: `utc_now`, onupdate | Last modification timestamp  |
| `deleted_at`         | `DateTime(tz)` | Nullable                     | Soft-delete timestamp        |
| `avatar_url`         | `String(512)`  | Nullable                     | Profile picture URL          |
| `google_id`          | `String(255)`  | Unique, indexed, nullable    | Google OAuth subject ID      |
| `speaking_language`  | `String(10)`   | Default: `"en"`              | Preferred speaking language  |
| `listening_language` | `String(10)`   | Default: `"en"`              | Preferred listening language |
| `user_role`          | `String(50)`   | Default: `"user"`, indexed   | Role: `"user"` or `"admin"`  |

### VerificationToken

| Column       | Type           | Constraints               | Description                     |
|--------------|----------------|---------------------------|---------------------------------|
| `id`         | `Integer`      | PK, indexed               | Auto-increment identifier       |
| `user_id`    | `UUID`         | FK → `users.id`, not null | Owning user                     |
| `token`      | `String(36)`   | Unique, indexed, not null | UUID v4 string                  |
| `expires_at` | `DateTime(tz)` | Not null                  | Default: 24 hours from creation |
| `created_at` | `DateTime(tz)` | Not null                  | Token creation timestamp        |

### PasswordResetToken

| Column       | Type           | Constraints               | Description                                    |
|--------------|----------------|---------------------------|------------------------------------------------|
| `id`         | `Integer`      | PK, indexed               | Auto-increment identifier                      |
| `user_id`    | `UUID`         | FK → `users.id`, not null | Owning user                                    |
| `token`      | `String(36)`   | Unique, indexed, not null | UUID v4 string                                 |
| `expires_at` | `DateTime(tz)` | Not null                  | Set from `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` |
| `created_at` | `DateTime(tz)` | Not null                  | Token creation timestamp                       |

---

## Request / Response Schemas

### Enums

#### `SupportedLanguage`

| Value | Label      |
|-------|------------|
| `en`  | English    |
| `fr`  | French     |
| `de`  | German     |
| `es`  | Spanish    |
| `it`  | Italian    |
| `pt`  | Portuguese |

#### `UserRole`

| Value   | Description             |
|---------|-------------------------|
| `user`  | Standard user (default) |
| `admin` | Administrator           |

### Request Schemas

| Schema                      | Used By                     | Fields                                                                                                                                        |
|-----------------------------|-----------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| `SignupRequest`             | `POST /signup`              | `email`, `password` (min 8), `confirm_password`, `accepted_terms` (must be `true`), `full_name?`, `speaking_language?`, `listening_language?` |
| `LoginRequest`              | `POST /login`               | `email`, `password`                                                                                                                           |
| `ResendVerificationRequest` | `POST /resend-verification` | `email`                                                                                                                                       |
| `ForgotPasswordRequest`     | `POST /forgot-password`     | `email`                                                                                                                                       |
| `ResetPasswordRequest`      | `POST /reset-password`      | `token` (min 1), `new_password` (min 8)                                                                                                       |
| `ChangePasswordRequest`     | `POST /change-password`     | `current_password`, `new_password` (min 8)                                                                                                    |

### Response Schemas

| Schema                  | Used By               | Fields                                                                                                                       |
|-------------------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------|
| `SignupResponse`        | `POST /signup`        | `id`, `email`, `full_name`, `speaking_language`, `listening_language`, `user_role`, `is_active`, `is_verified`, `created_at` |
| `LoginResponse`         | `POST /login`         | `access_token`, `user_id`, `token_type`, `expires_in`                                                                        |
| `VerifyEmailResponse`   | `GET /verify-email`   | `status` (= `"ok"`), `message`                                                                                               |
| `ActionAcknowledgement` | Multiple endpoints    | `status` (= `"ok"`), `message`                                                                                               |
| `RefreshTokenResponse`  | `POST /refresh-token` | `access_token`, `token_type`, `expires_in`                                                                                   |

---

## Error Codes Reference

All errors follow a consistent JSON structure:

```json
{
  "code": "ERROR_CODE",
  "message": "Human-readable error description."
}
```

### Complete Error Code Table

| Code                       | HTTP Status | Endpoint(s)                                      | Description                                      |
|----------------------------|-------------|--------------------------------------------------|--------------------------------------------------|
| `EMAIL_ALREADY_REGISTERED` | 409         | `/signup`                                        | Duplicate email at registration                  |
| `MISSING_CREDENTIALS`      | 400         | `/login`                                         | Empty request body on login                      |
| `INVALID_CREDENTIALS`      | 401         | `/login`, auth guard                             | Wrong email/password or invalid JWT              |
| `EMAIL_NOT_VERIFIED`       | 403         | `/login`                                         | Attempting login before email verification       |
| `ACCOUNT_DELETED`          | 403         | `/login`, auth guard                             | Account has been soft-deleted                    |
| `ACCOUNT_LOCKED`           | 403         | `/login`, `/google/callback`                     | Locked after too many failed attempts            |
| `ACCOUNT_DEACTIVATED`      | 403         | `/refresh-token`, `/google/callback`, auth guard | Account deactivated or deleted                   |
| `MISSING_TOKEN`            | 400/401     | `/verify-email`, auth guard                      | Token not provided                               |
| `INVALID_TOKEN`            | 400         | `/verify-email`                                  | Token is malformed or not found                  |
| `TOKEN_EXPIRED`            | 400         | `/verify-email`                                  | Verification token has expired                   |
| `TOKEN_REVOKED`            | 401         | Auth guard                                       | Access token has been blacklisted                |
| `INVALID_RESET_TOKEN`      | 400         | `/reset-password`                                | Reset token not found or user missing            |
| `RESET_TOKEN_EXPIRED`      | 400         | `/reset-password`                                | Password reset token has expired                 |
| `SAME_PASSWORD`            | 400         | `/reset-password`, `/change-password`            | New password matches the current one             |
| `INCORRECT_PASSWORD`       | 400         | `/change-password`                               | Current password verification failed             |
| `MISSING_REFRESH_TOKEN`    | 401         | `/refresh-token`                                 | No refresh token cookie present                  |
| `INVALID_REFRESH_TOKEN`    | 401         | `/refresh-token`                                 | Refresh token JWT is invalid or expired          |
| `REFRESH_TOKEN_REUSE`      | 401         | `/refresh-token`                                 | Revoked token was replayed — all sessions killed |
| `INVALID_OAUTH_STATE`      | 400         | `/google/callback`                               | CSRF state token invalid or expired              |
| `INVALID_OAUTH_PROFILE`    | 400         | `/google/callback`                               | Google profile missing email address             |
| `OAUTH_PROVIDER_ERROR`     | 502         | `/google/callback`                               | Failed to communicate with Google APIs           |

---

## Configuration Reference

All values are configurable via environment variables or `.env` file.

### Security & Tokens

| Setting                               | Default                    | Description                                         |
|---------------------------------------|----------------------------|-----------------------------------------------------|
| `SECRET_KEY`                          | `"placeholder_secret_key"` | JWT signing key. **Must be changed in production.** |
| `ALGORITHM`                           | `"HS256"`                  | JWT signing algorithm                               |
| `ACCESS_TOKEN_EXPIRE_MINUTES`         | `60`                       | Access token lifetime in minutes                    |
| `REFRESH_TOKEN_EXPIRE_DAYS`           | `7`                        | Refresh token lifetime in days                      |
| `VERIFICATION_TOKEN_EXPIRE_HOURS`     | `24`                       | Email verification token lifetime in hours          |
| `PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` | `60`                       | Password reset token lifetime in minutes            |

### Account Lockout

| Setting                     | Default | Description                         |
|-----------------------------|---------|-------------------------------------|
| `MAX_FAILED_LOGIN_ATTEMPTS` | `5`     | Consecutive failures before lockout |
| `ACCOUNT_LOCKOUT_DAYS`      | `5`     | Duration of lockout in days         |

### Infrastructure

| Setting             | Default                   | Description                              |
|---------------------|---------------------------|------------------------------------------|
| `REDIS_HOST`        | `"localhost"`             | Redis server hostname                    |
| `REDIS_PORT`        | `6379`                    | Redis server port                        |
| `FRONTEND_BASE_URL` | `"http://localhost:3000"` | Base URL for email links (verify, reset) |
| `API_V1_STR`        | `"/api/v1"`               | API version prefix                       |

### Google OAuth

| Setting                | Default | Description                                              |
|------------------------|---------|----------------------------------------------------------|
| `GOOGLE_CLIENT_ID`     | `None`  | OAuth client ID (required for OAuth)                     |
| `GOOGLE_CLIENT_SECRET` | `None`  | OAuth client secret (required for OAuth)                 |
| `GOOGLE_REDIRECT_URI`  | `None`  | Callback URL registered with Google (required for OAuth) |

---

## Internal Services

### AuthService

The core business logic coordinator. Injected with all subsystem dependencies via FastAPI's DI.

**Constructor Dependencies:**
- `db: Session` — SQLAlchemy database session
- `security_service: SecurityService` — Password hashing and JWT operations
- `email_producer: EmailProducerService` — Async email dispatch via Kafka
- `auth_verification_service: AuthVerificationService` — Verification token CRUD
- `lockout_svc: AccountLockoutService` — Brute-force protection
- `token_store: TokenStoreService` — Redis refresh token and blacklist management

**Public Methods:**

| Method                                                         | Description                                          |
|----------------------------------------------------------------|------------------------------------------------------|
| `signup(user_in, frontend_base_url)`                           | Create user, generate verification token, send email |
| `login(payload)`                                               | Validate credentials, check guards, issue tokens     |
| `forgot_password(email, frontend_base_url)`                    | Generate reset token, send email                     |
| `reset_password(token, new_password)`                          | Validate token, update password, revoke sessions     |
| `change_password(user, current_password, new_password)`        | Verify current, update hash, revoke sessions         |
| `logout(email, access_jti, access_ttl_remaining, refresh_jti)` | Blacklist AT, revoke RT                              |
| `refresh_token(raw_token)`                                     | Rotate refresh token with reuse detection            |
| `resolve_oauth_user(email, google_id, name, avatar_url)`       | Find/create/link OAuth user, issue tokens            |

### TokenStoreService

Redis-backed service managing refresh token JTIs and access token blacklisting.

**Redis Key Schemas:**

| Key Pattern                      | TTL                   | Purpose                       |
|----------------------------------|-----------------------|-------------------------------|
| `refresh_token:{email}:{jti}`    | Matches token expiry  | Valid refresh token indicator |
| `blacklisted_access_token:{jti}` | Remaining AT lifetime | Blacklisted access token      |

**Public Methods:**

| Method                                        | Description                                |
|-----------------------------------------------|--------------------------------------------|
| `save_refresh_token(email, jti, ttl_seconds)` | Persist a new refresh token JTI            |
| `revoke_refresh_token(email, jti)`            | Delete a specific JTI                      |
| `is_refresh_token_valid(email, jti)`          | Check if JTI exists (not revoked/expired)  |
| `revoke_all_user_tokens(email)`               | SCAN + pipeline delete all JTIs for a user |
| `blacklist_access_token(jti, ttl_seconds)`    | Add AT JTI to blacklist                    |
| `is_access_token_blacklisted(jti)`            | Check if AT is blacklisted                 |

### AccountLockoutService

Redis-backed brute-force protection tracking failed login attempts.

**Public Methods:**

| Method                         | Description                                  |
|--------------------------------|----------------------------------------------|
| `is_locked(email)`             | Check if account is currently locked         |
| `record_failed_attempt(email)` | Increment counter; lock if threshold reached |
| `reset_attempts(email)`        | Clear failure counter (on successful login)  |

### AuthVerificationService

Manages verification token lifecycle for email verification.

**Public Methods:**

| Method                               | Description                                    |
|--------------------------------------|------------------------------------------------|
| `create_verification_token(user_id)` | Generate and persist a new `VerificationToken` |
| `verify_email(token)`                | Validate token, activate user, delete token    |
| `resend_verification_email(email)`   | Delete old tokens, create new one, send email  |

### GoogleOAuthService

Handles the Google OAuth 2.0 authorization code flow.

**Public Methods:**

| Method                        | Description                                         |
|-------------------------------|-----------------------------------------------------|
| `build_auth_url(state)`       | Construct the Google consent URL with CSRF state    |
| `exchange_code(code)`         | Exchange authorization code for Google access token |
| `get_user_info(access_token)` | Fetch user profile from Google's userinfo endpoint  |

---

## Usage Examples

### cURL: Register a New User

```bash
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "jane@example.com",
    "password": "mySecureP4ss!",
    "full_name": "Jane Doe",
    "speaking_language": "en",
    "listening_language": "fr"
  }'
```

### cURL: Login and Capture Refresh Cookie

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{
    "email": "jane@example.com",
    "password": "mySecureP4ss!"
  }'
```

### cURL: Refresh Token

```bash
curl -X POST http://localhost:8000/api/v1/auth/refresh-token \
  -b cookies.txt \
  -c cookies.txt
```

### cURL: Authenticated Request (Change Password)

```bash
curl -X POST http://localhost:8000/api/v1/auth/change-password \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{
    "current_password": "mySecureP4ss!",
    "new_password": "evenMoreSecure!"
  }'
```

### cURL: Logout

```bash
curl -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer <access_token>" \
  -b cookies.txt
```
