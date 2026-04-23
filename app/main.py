from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from . import db_registry, history, schema_dsl, schema_introspect
from .config import settings
from .executor import execute
from .generator import generate_sql, reset_classifier
from .schema_dsl import get_schema, invalidate_cache
from .training.fit import fit as fit_classifier
from .validator import ValidationError, validate_and_rewrite


app = FastAPI(title="NL2SQL", version="0.3.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


@dataclass
class ActiveDB:
    name: str
    url: str
    schema_file: str
    table_count: int


def _resolve_active() -> ActiveDB:
    """Registry wins if an active DB is configured; otherwise fall back to env."""
    entry = db_registry.get_active(settings.registry_db)
    if entry is not None:
        return ActiveDB(
            name=entry.name,
            url=entry.url(),
            schema_file=entry.schema_file,
            table_count=entry.table_count,
        )
    return ActiveDB(
        name=settings.effective_db_name or "default",
        url=settings.db_url,
        schema_file=settings.schema_file,
        table_count=0,
    )


@app.on_event("startup")
def _startup() -> None:
    history.init(settings.history_db)
    db_registry.init(settings.registry_db)
    try:
        get_schema(_resolve_active().schema_file)
    except FileNotFoundError:
        pass
    # Train classifier on seeds if no model yet — takes <1s and means the
    # first cold request lands on ML, not the regex fallback.
    if not Path(settings.intent_model_path).exists():
        try:
            fit_classifier(settings.history_db, settings.intent_model_path)
            reset_classifier()
        except Exception:  # noqa: BLE001
            # Training is best-effort; fallback still works.
            pass


# ---------- Ask / history / schema ----------

class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    execute: bool = True


class AskResponse(BaseModel):
    sql: str | None
    explanation: str
    confidence: float
    intent: str = ""
    tables_used: list[str] = []
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False
    model: str = "local-rule+tfidf"
    error: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    active = _resolve_active()
    try:
        schema = get_schema(active.schema_file)
        table_count = len(schema.tables)
        database = schema.database
    except FileNotFoundError:
        table_count = 0
        database = active.name
    model_ready = Path(settings.intent_model_path).exists()
    return {
        "status": "ok",
        "database": database,
        "active_db": active.name,
        "tables": table_count,
        "model": "local-rule+tfidf",
        "model_trained": model_ready,
    }


@app.get("/api/schema")
def schema_endpoint() -> dict[str, Any]:
    active = _resolve_active()
    try:
        schema = get_schema(active.schema_file)
    except FileNotFoundError as e:
        raise HTTPException(404, "No schema loaded. Configure a database first.") from e
    return {
        "database": schema.database,
        "description": schema.description,
        "tables": [
            {
                "name": t.name,
                "description": t.description,
                "columns": [{"name": c.name, "type": c.type} for c in t.columns],
            }
            for t in schema.tables
        ],
    }


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    active = _resolve_active()
    try:
        schema = get_schema(active.schema_file)
    except FileNotFoundError as e:
        raise HTTPException(400, "No schema loaded. Configure a database first.") from e

    try:
        gen = generate_sql(
            req.question, schema, settings.max_rows, settings.intent_model_path
        )
    except Exception as e:  # noqa: BLE001
        history.record(
            settings.history_db,
            question=req.question,
            sql=None,
            status="gen_error",
            error=str(e),
        )
        raise HTTPException(500, f"Generation failed: {e}") from e

    if not gen.sql:
        history.record(
            settings.history_db,
            question=req.question,
            sql=None,
            status="no_sql",
            error=gen.explanation,
            model=gen.model,
        )
        return AskResponse(
            sql=None,
            explanation=gen.explanation or "Could not produce SQL for this question.",
            confidence=gen.confidence,
            intent=gen.intent,
            model=gen.model,
            error="no_sql_generated",
        )

    try:
        validated = validate_and_rewrite(gen.sql, schema, settings.max_rows)
    except ValidationError as e:
        history.record(
            settings.history_db,
            question=req.question,
            sql=gen.sql,
            status="invalid_sql",
            error=str(e),
            model=gen.model,
        )
        return AskResponse(
            sql=gen.sql,
            explanation=gen.explanation,
            confidence=gen.confidence,
            intent=gen.intent,
            model=gen.model,
            error=f"validation_failed: {e}",
        )

    if not req.execute:
        return AskResponse(
            sql=validated.sql,
            explanation=gen.explanation,
            confidence=gen.confidence,
            intent=gen.intent,
            tables_used=validated.tables_used,
            model=gen.model,
        )

    try:
        exec_result = execute(
            validated.sql,
            active.url,
            settings.statement_timeout_ms,
            settings.max_rows,
        )
    except Exception as e:  # noqa: BLE001
        history.record(
            settings.history_db,
            question=req.question,
            sql=validated.sql,
            status="exec_error",
            error=str(e),
            tables_used=validated.tables_used,
            model=gen.model,
        )
        return AskResponse(
            sql=validated.sql,
            explanation=gen.explanation,
            confidence=gen.confidence,
            intent=gen.intent,
            tables_used=validated.tables_used,
            model=gen.model,
            error=f"execution_failed: {e}",
        )

    history.record(
        settings.history_db,
        question=req.question,
        sql=validated.sql,
        status="ok",
        row_count=exec_result.row_count,
        elapsed_ms=exec_result.elapsed_ms,
        tables_used=validated.tables_used,
        model=gen.model,
    )

    return AskResponse(
        sql=validated.sql,
        explanation=gen.explanation,
        confidence=gen.confidence,
        intent=gen.intent,
        tables_used=validated.tables_used,
        columns=exec_result.columns,
        rows=exec_result.rows,
        row_count=exec_result.row_count,
        elapsed_ms=exec_result.elapsed_ms,
        truncated=exec_result.truncated,
        model=gen.model,
    )


@app.get("/api/history")
def history_endpoint(limit: int = 50) -> dict[str, Any]:
    return {"items": history.list_recent(settings.history_db, limit=limit)}


@app.post("/api/train")
def train_endpoint() -> dict[str, Any]:
    """Retrain the intent classifier from SEED + successful history rows."""
    report = fit_classifier(settings.history_db, settings.intent_model_path)
    reset_classifier()
    return report


# ---------- Database registry ----------

class DatabaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    dbname: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = ""
    activate: bool = True


_IN_DOCKER = Path("/.dockerenv").exists()
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _normalize_host(host: str) -> str:
    """Inside a container loopback addresses point at the container itself.
    Rewrite to host.docker.internal so SSH tunnels / local DBs work."""
    if _IN_DOCKER and host.strip().lower() in _LOOPBACK_HOSTS:
        return "host.docker.internal"
    return host


def _build_url(host: str, port: int, dbname: str, username: str, password: str) -> str:
    host = _normalize_host(host)
    return f"postgresql+psycopg2://{username}:{password}@{host}:{port}/{dbname}"


def _verify_connection(url: str) -> None:
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 5})
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Connection test failed: {e}") from e


@app.get("/api/databases")
def list_databases() -> dict[str, Any]:
    entries = db_registry.list_all(settings.registry_db)
    return {"items": [e.to_public_dict() for e in entries]}


@app.post("/api/databases")
def add_database(req: DatabaseCreate) -> dict[str, Any]:
    if db_registry.get_by_name(settings.registry_db, req.name) is not None:
        raise HTTPException(409, f"A database named '{req.name}' is already configured.")

    resolved_host = _normalize_host(req.host)
    url = _build_url(resolved_host, req.port, req.dbname, req.username, req.password)
    _verify_connection(url)

    safe = db_registry.sanitize_name(req.name)
    schema_path = SCHEMA_DIR / f"{safe}.yml"

    try:
        table_count = schema_introspect.generate_and_write(url, schema_path, req.dbname)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Schema generation failed: {e}") from e

    entry = db_registry.insert(
        settings.registry_db,
        name=req.name,
        host=resolved_host,
        port=req.port,
        dbname=req.dbname,
        username=req.username,
        password=req.password,
        schema_file=str(schema_path),
        table_count=table_count,
    )

    if req.activate:
        db_registry.set_active(settings.registry_db, req.name)
        invalidate_cache()

    return {"item": db_registry.get_by_name(settings.registry_db, req.name).to_public_dict()}  # type: ignore[union-attr]


@app.post("/api/databases/{name}/activate")
def activate_database(name: str) -> dict[str, Any]:
    entry = db_registry.get_by_name(settings.registry_db, name)
    if entry is None:
        raise HTTPException(404, f"No database named '{name}'.")
    db_registry.set_active(settings.registry_db, name)
    invalidate_cache()
    return {"item": db_registry.get_by_name(settings.registry_db, name).to_public_dict()}  # type: ignore[union-attr]


@app.post("/api/databases/{name}/regenerate")
def regenerate_database(name: str) -> dict[str, Any]:
    entry = db_registry.get_by_name(settings.registry_db, name)
    if entry is None:
        raise HTTPException(404, f"No database named '{name}'.")
    try:
        count = schema_introspect.generate_and_write(entry.url(), Path(entry.schema_file), entry.dbname)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Schema generation failed: {e}") from e
    db_registry.update_table_count(settings.registry_db, name, count)
    invalidate_cache()
    return {"item": db_registry.get_by_name(settings.registry_db, name).to_public_dict()}  # type: ignore[union-attr]


@app.delete("/api/databases/{name}")
def delete_database(name: str) -> dict[str, Any]:
    entry = db_registry.get_by_name(settings.registry_db, name)
    if entry is None:
        raise HTTPException(404, f"No database named '{name}'.")
    schema_path = Path(entry.schema_file)
    try:
        if SCHEMA_DIR in schema_path.resolve().parents and schema_path.exists():
            schema_path.unlink()
    except (OSError, RuntimeError):
        pass
    db_registry.delete(settings.registry_db, name)
    invalidate_cache()
    return {"deleted": name}


# ---------- Static ----------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
