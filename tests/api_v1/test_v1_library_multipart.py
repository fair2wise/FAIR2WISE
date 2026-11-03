"""T35: the minimal multipart reader behind ``POST /v1/library/papers`` (no ``python-multipart`` in the environment)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.multipart_upload import boundary_of, extract_file

B = "XyZ123"


def body(*parts: bytes, boundary: str = B) -> bytes:
    out = b""
    for part in parts:
        out += b"--" + boundary.encode() + b"\r\n" + part + b"\r\n"
    return out + b"--" + boundary.encode() + b"--\r\n"


def part(name: str, filename: str | None, data: bytes, extra: bytes = b"") -> bytes:
    disp = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else "")
    return disp.encode() + b"\r\n" + extra + b"\r\n" + data


def test_extracts_the_named_file_part_with_binary_payload():
    data = b"%PDF-1.4\r\n\x00\xff\r\n\r\n--not-the-boundary\r\n" + bytes(range(256))
    b = body(part("note", None, b"hello"), part("file", "a b.pdf", data, b"Content-Type: application/pdf\r\n"))
    assert extract_file(b, B.encode(), "file") == ("a b.pdf", data)


def test_missing_wrong_field_and_non_file_part_are_422():
    for blob in (body(part("other", "a.pdf", b"x")), body(part("file", None, b"x")), b"garbage", b"--XyZ123\r\nno end",
                 b""):
        with pytest.raises(HTTPException) as err:
            extract_file(blob, B.encode(), "file")
        assert err.value.status_code == 422


def test_unicode_and_quoted_filenames():
    assert extract_file(body(part("file", "résumé.pdf", b"x")), B.encode(), "file")[0] == "résumé.pdf"


def test_boundary_of():
    assert boundary_of(f"multipart/form-data; boundary={B}") == B.encode()
    assert boundary_of(f'multipart/form-data; boundary="{B}"') == B.encode()
    for bad, code in (("application/json", 415), (None, 415), ("multipart/form-data", 422),
                      ("multipart/form-data; boundary=" + "x" * 300, 422)):
        with pytest.raises(HTTPException) as err:
            boundary_of(bad)
        assert err.value.status_code == code
