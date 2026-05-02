# FluentMeet Models Documentation

> **Package Location:** `/app/models`
> **Purpose:** Centralized SQLAlchemy Declarative Base and Alembic Schema Aggregation.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
  - [The Declarative Base (`base.py`)](#the-declarative-base-basepy)
  - [Model Aggregation (`__init__.py`)](#model-aggregation-__init__py)

---

## Overview

The `app/models` package serves as the foundational data layer configuration for the FluentMeet application. Unlike older monolithic architectures that place all SQLAlchemy models in a single `models.py` file, FluentMeet uses a domain-driven structure where models live inside their respective feature modules (e.g., `app/modules/meeting/models.py`).

To satisfy SQLAlchemy and Alembic strict requirements for database schema generation, this package acts as the central initialization and aggregation point for the ORM.

---

## Architecture

This package solves the classic ORM bootstrap problem by centralizing the `Base` class, which every module imports, and then importing those completed models back into an `__init__.py` so Alembic's `env.py` has a single, complete metadata registry object to inspect during migrations.

```
┌─────────────────────────┐         ┌─────────────────────────┐
│  app/modules/auth/      │         │ app/modules/meeting/    │
│      models.py          │         │      models.py          │
│ (User, Tokens, etc.)    │         │ (Room, Participant...)  │
└────────────┬────────────┘         └────────────┬────────────┘
             │                                   │
             ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    app/models/__init__.py                   │
│                                                             │
│  from app.models.base import Base                           │
│  from app.modules.meeting.models import Room, ...           │
│                                                             │
│  __all__ = ["Base", "Room", ...]                            │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                       alembic/env.py                        │
│                                                             │
│  from app.models import Base                                │
│  target_metadata = Base.metadata                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### The Declarative Base (`base.py`)

This file contains the absolute minimum required to establish the SQLAlchemy 2.0 ORM base using `DeclarativeBase`.

```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

**Why it's isolated:**
Decoupling the `Base` metadata from the actual Model files prevents Circular Import errors between the central registry and the feature modules that need to inherit from it.

### Model Aggregation (`__init__.py`)

This file aggregates the distributed ORM models, making them easily accessible.

It currently exports:
- `Base` (The core declarative metadata class)
- `Room` (from `app.modules.meeting.models`)
- `Participant` (from `app.modules.meeting.models`)
- `MeetingInvitation` (from `app.modules.meeting.models`)

*(Note: It is recommended that as new modules are created—such as the `auth` module—their respective ORM entities e.g., `User`, are also imported into this initialization file to ensure complete coverage by the Alembic auto-migration tool.)*
