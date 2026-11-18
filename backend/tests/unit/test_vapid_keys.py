"""VAPID raw-scalar/browser-point compatibility without optional delivery imports."""

import base64
import builtins
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend.services.sharing.vapid_keys import get_or_create_vapid_keys


def _encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _keys():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, {
        "private_key": _encoded(private.private_numbers().private_value.to_bytes(32, "big")),
        "public_key": _encoded(
            private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        ),
    }


def test_fresh_keys_match_raw_scalar_and_browser_point_without_py_vapid(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def no_optional(name, *args, **kwargs):
        if name == "py_vapid" or name.startswith("py_vapid."):
            raise ModuleNotFoundError("py_vapid is intentionally absent", name="py_vapid")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_optional)
    nested = tmp_path / "nested"
    keys = get_or_create_vapid_keys(nested)
    private_bytes = base64.urlsafe_b64decode(keys["private_key"] + "=")
    public_bytes = base64.urlsafe_b64decode(keys["public_key"] + "=")
    assert len(private_bytes) == 32 and len(public_bytes) == 65 and public_bytes[0] == 4
    private = ec.derive_private_key(int.from_bytes(private_bytes, "big"), ec.SECP256R1())
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_bytes)
    signature = private.sign(b"vapid-compatibility", ec.ECDSA(hashes.SHA256()))
    public.verify(signature, b"vapid-compatibility", ec.ECDSA(hashes.SHA256()))
    path = nested / "vapid.json"
    before = path.read_bytes()
    assert get_or_create_vapid_keys(nested) == keys
    assert path.read_bytes() == before
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("encoding", ["raw", "der-pkcs8", "der-sec1"])
@pytest.mark.parametrize("public_present", [True, False])
def test_existing_py_vapid_compatible_private_key_is_never_rotated_or_rewritten(tmp_path, encoding, public_present):
    private, keys = _keys()
    if encoding != "raw":
        form = (
            serialization.PrivateFormat.PKCS8
            if encoding == "der-pkcs8"
            else serialization.PrivateFormat.TraditionalOpenSSL
        )
        keys["private_key"] = _encoded(
            private.private_bytes(serialization.Encoding.DER, form, serialization.NoEncryption())
        )
    expected = dict(keys)
    if not public_present:
        del keys["public_key"]
    path = tmp_path / "vapid.json"
    path.write_text(json.dumps(keys, indent=4) + "\n")
    before = path.read_bytes()
    assert get_or_create_vapid_keys(tmp_path) == expected
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "defect", ["json", "missing_private", "invalid_private", "zero_scalar", "mismatch", "wrong_curve"]
)
def test_invalid_existing_material_requires_explicit_repair_without_overwrite(tmp_path, defect):
    _, keys = _keys()
    if defect == "missing_private":
        del keys["private_key"]
    elif defect == "invalid_private":
        keys["private_key"] = "not-a-valid-private-key!"
    elif defect == "zero_scalar":
        keys["private_key"] = _encoded(bytes(32))
    elif defect == "mismatch":
        keys["public_key"] = _keys()[1]["public_key"]
    elif defect == "wrong_curve":
        keys["private_key"] = _encoded(
            ec.generate_private_key(ec.SECP384R1()).private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    path = tmp_path / "vapid.json"
    before = b"invalid json!" if defect == "json" else json.dumps(keys).encode()
    path.write_bytes(before)
    with pytest.raises(ValueError, match="explicit repair is required") as error:
        get_or_create_vapid_keys(tmp_path)
    assert keys.get("private_key", "sentinel") not in str(error.value)
    assert path.read_bytes() == before


def test_competing_initializer_does_not_overwrite_the_winning_keys(tmp_path, monkeypatch):
    _, winner = _keys()
    path = tmp_path / "vapid.json"
    original_open = os.open

    def competing_open(target, flags, mode):
        path.write_text(json.dumps(winner))
        return original_open(target, flags, mode)

    monkeypatch.setattr(os, "open", competing_open)
    assert get_or_create_vapid_keys(tmp_path) == winner
    assert json.loads(path.read_text()) == winner
