# FluentMeet Cloudinary SDK Documentation

> **Package Location:** `/app/external_services/cloudinary`
> **Purpose:** Abstracted Cloud Storage service wrapping the official Cloudinary Python SDK.

---

## Table of Contents

- [Overview](#overview)
- [Architecture & Setup](#architecture--setup)
- [Public Providers API (`service.py`)](#public-providers-api-servicepy)
- [Validation & Constants](#validation--constants)
- [Data Schemas (`schemas.py`)](#data-schemas-schemaspy)
- [Error Handling (`exceptions.py`)](#error-handling-exceptionspy)

---

## Overview

The `app/external_services/cloudinary` package provides a fully decoupled, strongly-typed layer over the `cloudinary` SDK. It exposes the `StorageService` to handle asynchronous file uploads limit-checks, MIME validations, and secure resource deletions without leaking Cloudinary's specific configuration logic into the rest of the application (e.g., User or Meeting routers).

---

## Architecture & Setup

### Initialization (`config.py`)

Configuration runs lazily utilizing an `ensure_configured()` interceptor. 
When the `StorageService` is instantiated for the first time by the `get_storage_service()` FastAPI dependency, it reaches out to `config.py` which pulls:

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

from the application `settings` and statically configures the `cloudinary.config(secure=True)` global. Tracking the bool state `_configured` prevents redundant config payload calls.

---

## Public Providers API (`service.py`)

The `StorageService` contains distinct semantic methods. Under the hood, these methods delegate to a private `_upload()` coroutine after rigorously enforcing validations.

### Upload Methods

All upload methods require a `FastAPI.UploadFile` and a target `folder`. They natively support providing an optional `public_id` to enforce naming conventions (like using a User UUID for their avatar so it automatically overwrites).

*   `upload_image(...)`
    *   **Enforces:** Image Mimetypes & Image Size Limit.
    *   **Features:** Supports passing a dictionary chunk `transformation` (e.g., bounding box cropping, face targeting) to natively crop representations on the CDN before resting.

*   `upload_video(...)`
    *   **Enforces:** Video Mimetypes & Video Size Limit.

*   `upload_raw(...)`
    *   **Enforces:** Static Mimetypes (PDFs, ZIPs, txt) and uses Image Size limit.

### Delete Method

*   `delete_asset(public_id: str, resource_type: str)`
    *   Provides targeted resource teardown ensuring GDPR erasure compliance on User and Asset destruction.

---

## Validation & Constants

The package proactively protects the API from malformed or malicious file dumps.
Defined in `constants.py`:

**MIME Types Allowed:**
*   **Images:** `image/jpeg`, `image/png`, `image/webp`, `image/gif`, `image/svg+xml`
*   **Videos:** `video/mp4`, `video/webm`, `video/quicktime`, `video/x-msvideo`
*   **Static:** `application/pdf`, `application/zip`, `text/plain`, `text/csv`

**Folder Namespacing:**
Allows environment safety. `FOLDER_AVATARS`, `FOLDER_RECORDINGS`, `FOLDER_UPLOADS`.

**Size Validations:**
The internal `_validate_file` scans both the incoming HTTP `content_type` header string and calculates the `file.size` threshold dynamically against the respective `.env` limit mappings.

---

## Data Schemas (`schemas.py`)

To decouple the application router returns from Cloudinary's raw dynamic `dict` responses, outcomes are strictly marshaled via Pydantic:

### `UploadResult`
```json
{
    "public_id": "fluentmeet/avatars/abx123",
    "secure_url": "https://res.cloudinary.com/...",
    "resource_type": "image",
    "format": "webp",
    "bytes": 481023,
    "width": 400,
    "height": 400
}
```

### `DeleteResult`
```json
{
    "public_id": "fluentmeet/avatars/abx123",
    "result": "ok"
}
```

---

## Error Handling (`exceptions.py`)

The service raises context-specific HTTP exceptions inheriting appropriately from Base classes so FastAPI naturally constructs HTTP 400s or 500s:

*   **`FileValidationError`** (400 Bad Request): Thrown synchronously when MIME or Megabyte limits are exceeded before a network call is made. 
*   **`StorageUploadError`** (500 Internal Error): Thrown if the Cloudinary API rejects the data packet.
*   **`StorageDeleteError`** (500 Internal Error): Thrown if an explicit API delete fails fatally.
