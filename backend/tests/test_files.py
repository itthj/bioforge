"""File / dataset upload (Phase 6, slice 3): upload -> list -> download -> delete, the size/type
caps, and per-user isolation. Storage is redirected to a tmp dir so nothing touches /data."""

from __future__ import annotations

import pytest
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID


@pytest.fixture
def file_storage(tmp_path, monkeypatch):
    """Point the files API at a throwaway LocalStorage under tmp_path."""
    import bioforge.api.files as files_api
    from bioforge.storage.adapter import LocalStorage

    storage = LocalStorage(root_dir=str(tmp_path / "storage"))
    monkeypatch.setattr(files_api, "get_storage", lambda: storage)
    return storage


_FASTA = b">seq1\nATGCATGCATGCATGC\n>seq2\nTTTTGGGGCCCCAAAA\n"


async def _auth_header(client, email: str, password: str = "a-strong-password-1") -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": password})
    token = (await client.post("/auth/login", json={"email": email, "password": password})).json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def test_upload_list_download_delete_cycle(streaming_client, file_storage) -> None:
    pid = DEFAULT_PROJECT_ID

    up = await streaming_client.post(f"/projects/{pid}/files", files={"file": ("guides.fasta", _FASTA, "text/x-fasta")})
    assert up.status_code == 201, up.text
    meta = up.json()
    assert meta["filename"] == "guides.fasta"
    assert meta["size_bytes"] == len(_FASTA)
    assert len(meta["sha256"]) == 64
    file_id = meta["id"]

    listing = await streaming_client.get(f"/projects/{pid}/files")
    assert listing.status_code == 200
    assert [f["id"] for f in listing.json()] == [file_id]

    dl = await streaming_client.get(f"/projects/{pid}/files/{file_id}")
    assert dl.status_code == 200
    assert dl.content == _FASTA
    assert "guides.fasta" in dl.headers.get("content-disposition", "")

    assert (await streaming_client.delete(f"/projects/{pid}/files/{file_id}")).status_code == 204
    assert (await streaming_client.get(f"/projects/{pid}/files")).json() == []
    assert (await streaming_client.get(f"/projects/{pid}/files/{file_id}")).status_code == 404


async def test_disallowed_extension_is_rejected(streaming_client, file_storage) -> None:
    resp = await streaming_client.post(
        f"/projects/{DEFAULT_PROJECT_ID}/files", files={"file": ("malware.exe", b"MZ...", "application/octet-stream")}
    )
    assert resp.status_code == 415


async def test_empty_file_is_rejected(streaming_client, file_storage) -> None:
    resp = await streaming_client.post(
        f"/projects/{DEFAULT_PROJECT_ID}/files", files={"file": ("empty.fasta", b"", "text/x-fasta")}
    )
    assert resp.status_code == 422


async def test_oversize_file_is_rejected(streaming_client, file_storage, monkeypatch) -> None:
    monkeypatch.setattr(settings, "upload_max_bytes", 16)
    resp = await streaming_client.post(
        f"/projects/{DEFAULT_PROJECT_ID}/files", files={"file": ("big.txt", b"x" * 64, "text/plain")}
    )
    assert resp.status_code == 413


async def test_upload_to_missing_project_is_404(streaming_client, file_storage) -> None:
    resp = await streaming_client.post(
        "/projects/no-such-project/files", files={"file": ("x.fasta", _FASTA, "text/x-fasta")}
    )
    assert resp.status_code == 404


async def test_files_are_isolated_per_user(streaming_client, file_storage, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    alice = await _auth_header(streaming_client, "alice-f@lab.org")
    bob = await _auth_header(streaming_client, "bob-f@lab.org")
    await streaming_client.post("/projects", json={"id": "alice-data", "name": "A"}, headers=alice)

    up = await streaming_client.post(
        "/projects/alice-data/files", files={"file": ("a.fasta", _FASTA, "text/x-fasta")}, headers=alice
    )
    assert up.status_code == 201
    file_id = up.json()["id"]

    # Bob can't list, read, or upload into Alice's project.
    assert (await streaming_client.get("/projects/alice-data/files", headers=bob)).status_code == 404
    assert (await streaming_client.get(f"/projects/alice-data/files/{file_id}", headers=bob)).status_code == 404
    assert (
        await streaming_client.post(
            "/projects/alice-data/files", files={"file": ("b.fasta", _FASTA, "text/x-fasta")}, headers=bob
        )
    ).status_code == 404
    # Alice still can.
    assert (await streaming_client.get(f"/projects/alice-data/files/{file_id}", headers=alice)).status_code == 200
