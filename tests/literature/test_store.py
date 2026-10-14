"""T05 slice 1: content-addressed DocumentStore and the reversible legacy manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.modules.contracts.errors import ErrorCode
from app.modules.contracts.ports import DocumentStore
from app.modules.literature.legacy import (
    LegacyIndex, contained_regular_file, doi_key, legacy_filenames,
)
from app.modules.literature.store import (
    AssetKind, AssetTooLarge, FileDocumentStore, StoreError, StoreIntegrityError, ref_for_bytes,
)
from tests.literature.lit_fakes import als_user_ctx, local_ctx, minimal_pdf_bytes

PDF = minimal_pdf_bytes("one")
PDF2 = minimal_pdf_bytes("two")


@pytest.fixture
def store(tmp_path):
    return FileDocumentStore(tmp_path / "store")


def test_store_implements_document_store_port(store):
    assert isinstance(store, DocumentStore)
    ctx = local_ctx()
    ref = store.put(ctx, PDF, media_type="application/pdf", metadata={"kind": "source_pdf"})
    assert ref == ref_for_bytes(PDF)
    assert store.exists(ctx, ref)
    assert store.get(ctx, ref) == PDF
    assert list(store.list_refs(ctx)) == [ref]
    assert store.describe(ctx, ref).kind == AssetKind.SOURCE_PDF.value


def test_duplicate_pdf_is_stored_once_and_metadata_is_immutable(store):
    ctx = local_ctx()
    a = store.put_stream(ctx, [PDF], media_type="application/pdf",
                         metadata={"kind": "source_pdf", "doi": "10.1/a"})
    b = store.put_stream(ctx, [PDF[:10], PDF[10:]], media_type="application/pdf",
                         metadata={"kind": "source_pdf", "doi": "10.1/b"})
    assert a.ref == b.ref
    assert b.metadata["doi"] == "10.1/a"  # first write wins; aliases live in the legacy index
    assert len(list(store.list_refs(ctx))) == 1


def test_corrupt_asset_is_detected_on_read(store):
    ctx = local_ctx()
    ref = store.put(ctx, PDF, media_type="application/pdf", metadata={"kind": "source_pdf"})
    blob = store.root / "objects" / ref[7:9] / ref[7:]
    os.chmod(blob, 0o644)
    blob.write_bytes(PDF[:-5] + b"XXXXX")
    with pytest.raises(StoreIntegrityError):
        store.get(ctx, ref)
    assert not store.verify(ctx, ref)
    with pytest.raises(StoreIntegrityError):
        store.local_path(ctx, ref)


def test_byte_limit_and_signature_check_store_nothing(store):
    ctx = local_ctx()
    with pytest.raises(AssetTooLarge):
        store.put_stream(ctx, [b"%PDF-" + b"0" * 100], media_type="application/pdf", max_bytes=50)
    with pytest.raises(StoreError) as err:
        store.put_stream(ctx, [b"<html>not a pdf</html>"], media_type="application/pdf",
                         prefix_check=lambda head: head.startswith(b"%PDF-"))
    assert err.value.code is ErrorCode.INVALID_INPUT
    assert list(store.list_refs(ctx)) == []
    assert list((store.root / "tmp").iterdir()) == []


def test_refs_are_validated_and_never_paths(store):
    ctx = local_ctx()
    for bad in ("../../etc/passwd", "sha256:../x", "md5:abc", "sha256:" + "g" * 64):
        with pytest.raises(StoreError) as err:
            store.get(ctx, bad)
        assert err.value.code is ErrorCode.INVALID_INPUT
        assert store.exists(ctx, bad) is False
    ref = store.put(ctx, PDF, media_type="application/pdf", metadata={})
    asset = store.describe(ctx, ref)
    assert str(store.root) not in json.dumps(asset.__dict__)


def test_store_requires_context_and_corpus_scope(tmp_path):
    store = FileDocumentStore(tmp_path / "s", corpus="rsoxs")
    with pytest.raises(StoreError) as err:
        store.put(object(), PDF, media_type="application/pdf", metadata={})  # type: ignore[arg-type]
    assert err.value.code is ErrorCode.DENIED
    with pytest.raises(StoreError):
        store.put(als_user_ctx(corpus_scope=frozenset({"tomo"})), PDF, media_type="application/pdf", metadata={})
    ref = store.put(als_user_ctx(corpus_scope=frozenset({"rsoxs"})), PDF, media_type="application/pdf",
                    metadata={})
    assert store.get(local_ctx(), ref) == PDF


def test_reference_mode_keeps_legacy_root_and_verifies(tmp_path, store):
    ctx = local_ctx()
    legacy = tmp_path / "papers" / "rsoxs" / "2020"
    legacy.mkdir(parents=True)
    pdf = legacy / "10.1021_acs.macromol.0c01782.pdf"
    pdf.write_bytes(PDF)
    asset = store.reference_file(ctx, pdf, media_type="application/pdf", metadata={"kind": "source_pdf"})
    assert asset.mode == "reference"
    assert not (store.root / "objects" / asset.sha256[:2] / asset.sha256).exists()  # no copy
    assert store.get(ctx, asset.ref) == PDF
    pdf.write_bytes(PDF2)  # the legacy file is edited in place
    assert not store.verify(ctx, asset.ref)


def test_legacy_index_old_filenames_and_doi_lookup(tmp_path, store):
    ctx = local_ctx()
    root = tmp_path / "papers"
    (root / "rsoxs" / "2020").mkdir(parents=True)
    (root / "cache").mkdir()
    (root / "rsoxs" / "2020" / "10.1021_acs.macromol.0c01782.pdf").write_bytes(PDF)       # F2W stem
    (root / "cache" / "10_1039_C5TA01004G.pdf").write_bytes(PDF2)  # donor legacy stem, case
    (root / "cache" / "copy-of-first.pdf").write_bytes(PDF)                                # duplicate bytes
    (root / "cache" / "landing.pdf").write_bytes(b"<html>OSTI landing page</html>" * 50)   # not a PDF
    index = LegacyIndex(store)
    report = index.adopt_tree(ctx, "papers", root)
    assert sorted(report.adopted) == [
        "papers/cache/10_1039_C5TA01004G.pdf", "papers/cache/copy-of-first.pdf",
        "papers/rsoxs/2020/10.1021_acs.macromol.0c01782.pdf"]
    assert report.not_pdf == ["papers/cache/landing.pdf"]
    assert report.duplicates  # same bytes under two names reported, both kept
    assert index.find_by_doi("https://doi.org/10.1021/acs.macromol.0c01782") == ref_for_bytes(PDF)
    assert index.find_by_doi("10.1039/c5ta01004g") == ref_for_bytes(PDF2)
    assert index.find_by_doi("10.9999/unknown") is None
    assert index.lookup_path("papers", "CACHE/10_1039_c5ta01004g.pdf") == ref_for_bytes(PDF2)
    assert index.names_for(ref_for_bytes(PDF)) == [
        "papers/cache/copy-of-first.pdf", "papers/rsoxs/2020/10.1021_acs.macromol.0c01782.pdf"]
    again = index.adopt_tree(ctx, "papers", root)
    assert again.adopted == [] and len(again.already_indexed) == 3


def test_legacy_filename_conventions():
    names = legacy_filenames("https://doi.org/10.1038/s41563-019-0485-2")
    assert names[0] == "10.1038_s41563-019-0485-2.pdf"           # F2W DownloadAgent/harvest
    assert "10_1038_s41563_019_0485_2.pdf" in names                # donor _safe_stem
    assert doi_key("doi:10.1/ABC") == "10.1/abc" and doi_key("not a doi") == ""


def test_adopt_skips_symlinks_out_of_root(tmp_path, store):
    ctx = local_ctx()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(PDF)
    root = tmp_path / "papers"
    root.mkdir()
    (root / "link.pdf").symlink_to(outside / "secret.pdf")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    report = LegacyIndex(store).adopt_tree(ctx, "papers", root)
    assert report.adopted == []
    assert "papers/link.pdf" in report.skipped_symlinks
    assert not contained_regular_file(root, root / "linkdir" / "secret.pdf")


def test_cache_restore_is_reversible(tmp_path, store):
    ctx = local_ctx()
    root = tmp_path / "papers"
    root.mkdir()
    original = root / "10.1_x.pdf"
    original.write_bytes(PDF)
    index = LegacyIndex(store)
    index.adopt_tree(ctx, "papers", root, mode="copy")
    original.unlink()                                  # the old cache is lost
    restored = index.restore(ctx, "papers/10.1_x.pdf")
    assert restored == original and original.read_bytes() == PDF
    original.write_bytes(PDF2)                         # a different paper now sits at the name
    with pytest.raises(StoreError) as err:
        index.restore(ctx, "papers/10.1_x.pdf")
    assert err.value.code is ErrorCode.CONFLICT
    elsewhere = tmp_path / "restored"
    outcomes = index.restore_all(ctx, dest_root=elsewhere)
    assert outcomes == {"papers/10.1_x.pdf": "restored"}
    assert (elsewhere / "papers" / "10.1_x.pdf").read_bytes() == PDF
    exported = index.export()
    assert exported["files"]["papers/10.1_x.pdf"]["ref"] == ref_for_bytes(PDF)


def test_import_harvest_manifest_preserves_doi_and_flags_mismatch(tmp_path, store):
    ctx = local_ctx()
    repo = tmp_path / "repo"
    (repo / "papers" / "rsoxs" / "2021").mkdir(parents=True)
    good = repo / "papers" / "rsoxs" / "2021" / "10.1103_PhysRevB.1.pdf"
    good.write_bytes(PDF)
    preprint = repo / "papers" / "rsoxs" / "2021" / "10.48550_arXiv.2101.00001.pdf"
    preprint.write_bytes(PDF2)
    stale = repo / "papers" / "rsoxs" / "2021" / "10.1_stale.pdf"
    stale.write_bytes(PDF2 + b"x")
    import hashlib
    manifest = {"papers": [
        {"doi": "https://doi.org/10.1103/PhysRevB.1", "pdf_path": "papers/rsoxs/2021/10.1103_PhysRevB.1.pdf",
         "pdf_sha256": hashlib.sha256(PDF).hexdigest(),
         "versions": [{"kind": "preprint", "doi": "https://doi.org/10.48550/arXiv.2101.00001",
                       "pdf_path": "papers/rsoxs/2021/10.48550_arXiv.2101.00001.pdf"}]},
        {"doi": "10.1/stale", "pdf_path": "papers/rsoxs/2021/10.1_stale.pdf", "pdf_sha256": "0" * 64},
        {"doi": "10.1/gone", "pdf_path": "papers/rsoxs/2021/10.1_gone.pdf"},
        {"doi": "10.1/evil", "pdf_path": "../../etc/passwd"},
    ]}
    path = repo / "papers" / "rsoxs" / "manifest.json"
    path.write_text(json.dumps(manifest))
    index = LegacyIndex(store)
    report = index.import_harvest_manifest(ctx, path, repo_root=repo)
    assert report.dois_recorded == 2
    assert report.checksum_mismatch == ["repo/papers/rsoxs/2021/10.1_stale.pdf"]
    assert report.missing == ["repo/papers/rsoxs/2021/10.1_gone.pdf"]
    assert report.unreadable  # the escaping path
    assert index.refs_for_doi("10.1103/physrevb.1") == [ref_for_bytes(PDF)]
    assert index.refs_for_doi("10.48550/arxiv.2101.00001") == [ref_for_bytes(PDF2)]


def test_legacy_index_rejects_escaping_keys(store):
    with pytest.raises(StoreError):
        LegacyIndex.key("papers", "../x.pdf")
    with pytest.raises(StoreError):
        LegacyIndex.key("../root", "x.pdf")


def test_corrupt_legacy_index_is_not_guessed(store):
    index = LegacyIndex(store)
    index.path.parent.mkdir(parents=True, exist_ok=True)
    index.path.write_text("{not json")
    with pytest.raises(StoreError):
        index.read()


def test_store_default_location_is_explicit(tmp_path):
    # No default root: an isolated candidate store is always chosen by the caller.
    with pytest.raises(TypeError):
        FileDocumentStore()  # type: ignore[call-arg]
    assert Path(FileDocumentStore(tmp_path / "x").root) == tmp_path / "x"
