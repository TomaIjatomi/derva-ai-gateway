"""The audit log against real Postgres: schema, triggers, the insert-only role.

Runs when DERVA_TEST_PG_ADMIN_URL points at a database the test may reset, for
example ``postgresql+psycopg://postgres:pw@localhost/derva_test``. CI provides
one as a service container.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from derva.audit import AuditStore, EventType

ADMIN_URL = os.environ.get("DERVA_TEST_PG_ADMIN_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not ADMIN_URL, reason="DERVA_TEST_PG_ADMIN_URL not set"),
]
SCHEMA = Path(__file__).parents[1] / "sql" / "audit_schema.sql"
APP_PASSWORD = "derva-app-test"


@pytest.fixture
def admin_url() -> Iterator[str]:
    assert ADMIN_URL
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP VIEW IF EXISTS request_state"))
        conn.execute(text("DROP TABLE IF EXISTS audit_events"))
        conn.exec_driver_sql(SCHEMA.read_text())
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'derva_app') "
                f"THEN CREATE ROLE derva_app LOGIN PASSWORD '{APP_PASSWORD}'; END IF; END $$"
            )
        )
        conn.execute(text("GRANT derva_audit_writer TO derva_app"))
    engine.dispose()
    yield ADMIN_URL


@pytest.fixture
def app_store(admin_url: str) -> AuditStore:
    url = make_url(admin_url).set(username="derva_app", password=APP_PASSWORD)
    return AuditStore(url.render_as_string(hide_password=False), create_schema=False)


def test_writer_can_append_and_verify(app_store: AuditStore) -> None:
    for i in range(5):
        app_store.append(f"r-{i % 2}", EventType.RECEIVED, "key-1", {"i": i})
    assert app_store.verify().ok
    assert [e.seq for e in app_store.events()] == [1, 2, 3, 4, 5]
    assert app_store.request_state("r-1") == EventType.RECEIVED


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET actor = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"],
)
def test_writer_cannot_change_events(app_store: AuditStore, statement: str) -> None:
    app_store.append("r-1", EventType.RECEIVED, "key-1")
    with pytest.raises(DBAPIError, match="permission denied"), app_store.engine.begin() as conn:
        conn.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET actor = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"],
)
def test_even_the_owner_is_stopped_by_the_triggers(
    admin_url: str, app_store: AuditStore, statement: str
) -> None:
    app_store.append("r-1", EventType.RECEIVED, "key-1")
    engine = create_engine(admin_url)
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text(statement))
    engine.dispose()


def test_concurrent_writers_keep_one_unbroken_chain(admin_url: str, app_store: AuditStore) -> None:
    url = make_url(admin_url).set(username="derva_app", password=APP_PASSWORD)
    # Separate stores, as separate gateway instances would be.
    stores = [
        AuditStore(url.render_as_string(hide_password=False), create_schema=False) for _ in range(4)
    ]

    def write(store: AuditStore, n: int) -> None:
        for i in range(10):
            store.append(f"r-{n}", EventType.RECEIVED, f"key-{n}", {"i": i})

    threads = [threading.Thread(target=write, args=(s, n)) for n, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check = app_store.verify()
    assert check.ok and check.events_checked == 40, check
