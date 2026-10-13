"""Synthetic navigation metadata, never a grant or private participant reader."""
from catalog import Catalog
from assistant_store import Store, Problem
from service import dispatch
import pytest

def test_marker_is_versioned_metadata_and_foreign_reader_still_requires_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ID", "0")
    root=Store(tmp_path/"synthetic")
    try:
        listing=Catalog(root).listing()
        assert listing["navigation_contract"]=="owner-metadata-v1"
        request={"method":"GET","path":"owner/catalog","actor":{"scope":"app","app_id":123}}
        with pytest.raises(Problem) as denied:
            dispatch(root,request)
        assert denied.value.status==403
        root.set_config("owner_navigation_app_ids",[123])
        result=dispatch(root,request)
        assert result["navigation_contract"]=="owner-metadata-v1"
        assert "participants" not in result and "notes" not in result
    finally:
        root.db.close()
