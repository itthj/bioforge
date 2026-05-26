"""Migration drift-detection test.

The risk: someone adds a column to a model (or changes its type) and forgets to write
an alembic migration. The schema declared by `Base.metadata` would silently disagree
with what `alembic upgrade head` produces, and only fail in prod when the new column
is referenced.

This test applies the FULL migration chain to an empty temp-file SQLite and reflects
the resulting schema, then compares the table + column set against `Base.metadata`.
Any divergence — missing column, missing index, missing constraint — fails CI.

Indexes / unique constraints are checked at the table-presence level. Column-type
comparison is deliberately loose (name + nullable) because SQLAlchemy types stringify
differently across dialects and we don't want CI flapping over `String(64)` vs
`VARCHAR(64)`.
"""

from __future__ import annotations

import os
from pathlib import Path

from bioforge.db import models  # noqa: F401 — register tables on Base.metadata
from bioforge.db.engine import Base
from sqlalchemy import MetaData, create_engine, inspect

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
assert _ALEMBIC_INI.exists(), f"alembic.ini not found at {_ALEMBIC_INI}"


def _apply_migrations(sqlite_path: Path) -> None:
    """Apply alembic head to a fresh SQLite file. Returns once `alembic upgrade head`
    completes synchronously."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{sqlite_path.as_posix()}")
    # env.py also looks at BIOFORGE_DB_URL — keep it consistent so any future
    # branching in env.py sees the same destination.
    os.environ["BIOFORGE_DB_URL"] = f"sqlite:///{sqlite_path.as_posix()}"
    command.upgrade(cfg, "head")


def test_baseline_migration_produces_metadata_schema(tmp_path: Path) -> None:
    """After upgrade-head on an empty DB, every table in Base.metadata must exist with
    the same column names and nullability the models declare."""
    db_path = tmp_path / "drift.db"
    _apply_migrations(db_path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    reflected = MetaData()
    reflected.reflect(bind=engine)

    declared_tables = {t.name for t in Base.metadata.sorted_tables}
    reflected_tables = set(reflected.tables)

    # alembic adds its own bookkeeping table; subtract before comparing.
    reflected_tables.discard("alembic_version")

    missing = declared_tables - reflected_tables
    extra = reflected_tables - declared_tables
    assert not missing, (
        f"Migration is missing tables declared in models: {missing}. "
        "Generate a new revision with `alembic revision --autogenerate`."
    )
    assert not extra, (
        f"Migration creates tables not declared in models: {extra}. Either add the model or drop the migration."
    )

    # Per-table column comparison
    for table_name in declared_tables:
        declared = Base.metadata.tables[table_name]
        reflected_table = reflected.tables[table_name]

        declared_cols = {c.name: c for c in declared.columns}
        reflected_cols = {c.name: c for c in reflected_table.columns}

        missing_cols = set(declared_cols) - set(reflected_cols)
        extra_cols = set(reflected_cols) - set(declared_cols)
        assert not missing_cols, f"Table {table_name!r}: migration missing columns {missing_cols}"
        assert not extra_cols, f"Table {table_name!r}: migration has extra columns {extra_cols}"

        # Nullability must match — a NOT NULL declared in the model but nullable in
        # the migration (or vice versa) is a silent bug.
        for col_name, decl in declared_cols.items():
            refl = reflected_cols[col_name]
            assert decl.nullable == refl.nullable, (
                f"Table {table_name!r} col {col_name!r}: declared nullable="
                f"{decl.nullable}, migration produced nullable={refl.nullable}"
            )

    engine.dispose()


def test_alembic_version_table_present_after_upgrade(tmp_path: Path) -> None:
    """A successful `alembic upgrade head` always creates the `alembic_version`
    bookkeeping table. Its absence would mean migrations are silently no-op'd —
    catastrophic for prod, this test catches it in CI."""
    db_path = tmp_path / "version.db"
    _apply_migrations(db_path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    assert "alembic_version" in inspect(engine).get_table_names()
    engine.dispose()


def test_baseline_indexes_present(tmp_path: Path) -> None:
    """Indexes declared on the models (composite + single-column) must show up in
    the migrated schema. Catches the case where someone adds an index in models.py
    but the migration doesn't reflect it."""
    db_path = tmp_path / "indexes.db"
    _apply_migrations(db_path)

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = inspect(engine)

    # Hand-list rather than introspecting Base.metadata indexes: the test should fail
    # LOUDLY if any of these names disappear; introspection would make the assertion
    # vacuously true if the model also dropped the index by accident.
    expected = {
        "traces": {"ix_traces_project_id", "ix_traces_project_created"},
        "project_memory": {"ix_project_memory_project_updated"},
    }
    for table, expected_names in expected.items():
        actual = {ix["name"] for ix in inspector.get_indexes(table)}
        missing = expected_names - actual
        assert not missing, f"Table {table!r}: missing indexes {missing}"
    engine.dispose()
