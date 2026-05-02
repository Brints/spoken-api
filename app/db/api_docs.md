# FluentMeet DB Core Documentation

> **Package Location:** `/app/db`
> **Purpose:** Configures global synchronous SQLAlchemy engines and database dependency generators for FastAPI route bindings.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Public API (`session.py`)](#public-api-sessionpy)
- [Fallback Mechanisms](#fallback-mechanisms)

---

## Overview

The `app/db` package encapsulates all direct setup hooks binding ORM actions to the backing relational database (PostgreSQL predominantly). It securely controls Engine Lifecycles so connections are aggressively pooled rather than blindly spun up over HTTP requests.

---

## Architecture

This package adopts a synchronous SQLAlchemy standard methodology natively configuring `sqlalchemy.orm.Session`. Since heavy async code exists largely on the Kafka Real-time periphery (`app/services`) rather than standard user-CRUD actions, maintaining a stable sync DB API avoids extensive threading deadlocks.

To maximize usability across developers who configure robust `asyncpg` bindings globally via `.env`, the module intentionally coerces engine driver definitions natively upon launch mapping `asyncpg` configurations forcibly into `psycopg` parameters transparently without failing.

---

## Public API (`session.py`)

### Connection Contexts

#### `get_engine()`
Acts as a lazy-loaded Singleton accessor fetching the Global cache `_ENGINE_STATE`.
*   **Behavior:** Checks the dict. If `None`, triggers `create_engine` appending `pool_pre_ping=True` and binds the global `SessionLocal` macro.
*   **Returns:** Returns an actively configured `sqlalchemy.engine.Engine`.

#### `get_db()`
A standard Python Generator used specifically as a wrapper `Depends(get_db)` inside FastAPI Routers logic.
*   **Behavior:** Fetches a local thread-isolated `Session` bounding its lifecycle to a strict `try-finally` context forcing cleanup queries.
*   **Yields:** Returning a strict `sqlalchemy.orm.Session` reference resolving automatically explicitly returning connections back to the `Engine` pool upon return.

### Interceptors

#### `_coerce_sync_url(url)`
An internal helper bridging application boundaries.
*   **Behavior:** Actively intercepts the raw string extracted from `settings.DATABASE_URL`. If standard `+asyncpg` bindings are detected, it rewrites the string dynamically returning `postgresql+psycopg2://...`.
*   **Args:** `url` *(str)*
*   **Returns:** Mutated valid sync DB *(str)*.

---

## Fallback Mechanisms

To ensure Continuous Integration structures and simple developer test suites execute natively without manually spinning up Postgres docker containers universally, the DB context falls back elegantly natively inside `get_engine()`.

If `psycopg` is missing upon a load initialization triggered by an API request (`ModuleNotFoundError` exception thrown during driver bindings), the system suppresses the error logic falling back internally provisioning an ephemeral SQLite database on local paths (`sqlite:///./fluentmeet.db`) allowing raw ORM testing natively bypassing strict configurations.
