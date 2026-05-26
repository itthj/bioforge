"""Project + memory CRUD endpoint tests.

End-to-end through the FastAPI app via httpx.AsyncClient. The `streaming_client` fixture
(defined in conftest) hands us an in-memory app with a per-test SQLite + a pre-bootstrapped
`default-project` row.
"""

from __future__ import annotations

# --- Projects -------------------------------------------------------------------------


async def test_default_project_is_bootstrapped(streaming_client) -> None:
    response = await streaming_client.get("/projects/default-project")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "default-project"
    assert body["name"]


async def test_create_project(streaming_client) -> None:
    response = await streaming_client.post(
        "/projects",
        json={
            "id": "crispr-screen-2026",
            "name": "CRISPR screen 2026",
            "description": "Knockout screen targeting BRCA1 paralogs.",
            "organism": "Homo sapiens",
            "reference_genome": "GRCh38",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "crispr-screen-2026"
    assert body["organism"] == "Homo sapiens"


async def test_create_project_rejects_invalid_slug(streaming_client) -> None:
    response = await streaming_client.post(
        "/projects",
        json={"id": "Has Spaces And CAPS!", "name": "x"},
    )
    assert response.status_code == 422
    assert "slug" in response.text.lower()


async def test_create_project_conflict_on_duplicate_id(streaming_client) -> None:
    await streaming_client.post("/projects", json={"id": "dup", "name": "first"})
    response = await streaming_client.post("/projects", json={"id": "dup", "name": "second"})
    assert response.status_code == 409


async def test_list_projects_includes_default_and_created(streaming_client) -> None:
    await streaming_client.post("/projects", json={"id": "p1", "name": "P1"})
    response = await streaming_client.get("/projects")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert "default-project" in ids
    assert "p1" in ids


async def test_get_project_404(streaming_client) -> None:
    response = await streaming_client.get("/projects/does-not-exist")
    assert response.status_code == 404


async def test_patch_project_updates_fields(streaming_client) -> None:
    await streaming_client.post("/projects", json={"id": "pp", "name": "old"})
    response = await streaming_client.patch("/projects/pp", json={"name": "new", "organism": "Mus musculus"})
    assert response.status_code == 200
    assert response.json()["name"] == "new"
    assert response.json()["organism"] == "Mus musculus"


async def test_delete_project(streaming_client) -> None:
    await streaming_client.post("/projects", json={"id": "doomed", "name": "tmp"})
    response = await streaming_client.delete("/projects/doomed")
    assert response.status_code == 204
    response = await streaming_client.get("/projects/doomed")
    assert response.status_code == 404


# --- Memory ---------------------------------------------------------------------------


async def test_list_memory_empty_for_new_project(streaming_client) -> None:
    response = await streaming_client.get("/projects/default-project/memory")
    assert response.status_code == 200
    assert response.json() == []


async def test_put_memory_creates_then_updates(streaming_client) -> None:
    response = await streaming_client.put(
        "/projects/default-project/memory/preferred_organism",
        json={
            "value": "Homo sapiens",
            "kind": "preference",
            "rationale": "Default working species.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["key"] == "preferred_organism"
    assert body["value"] == "Homo sapiens"
    assert body["source"] == "user"

    # Update via PUT to the same key — should update, not create a duplicate.
    response = await streaming_client.put(
        "/projects/default-project/memory/preferred_organism",
        json={"value": "Mus musculus", "kind": "preference"},
    )
    assert response.status_code == 200
    assert response.json()["value"] == "Mus musculus"

    # List confirms only one entry.
    response = await streaming_client.get("/projects/default-project/memory")
    entries = response.json()
    assert len([e for e in entries if e["key"] == "preferred_organism"]) == 1


async def test_delete_memory(streaming_client) -> None:
    await streaming_client.put(
        "/projects/default-project/memory/tmp",
        json={"value": "x", "kind": "fact"},
    )
    response = await streaming_client.delete("/projects/default-project/memory/tmp")
    assert response.status_code == 204
    response = await streaming_client.delete("/projects/default-project/memory/tmp")
    assert response.status_code == 404


async def test_memory_404_for_unknown_project(streaming_client) -> None:
    response = await streaming_client.get("/projects/no-such/memory")
    assert response.status_code == 404
