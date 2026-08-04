from __future__ import annotations

import os

from backend.app.config import prepare_persistent_ca_bundle


def test_persistent_ca_bundle_survives_disposable_source(tmp_path, monkeypatch):
    source = tmp_path / "temporary-extraction" / "cacert.pem"
    source.parent.mkdir()
    source.write_text("test certificate bundle", encoding="utf-8")
    data_root = tmp_path / "app-data"

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr("certifi.where", lambda: str(source))

    target = prepare_persistent_ca_bundle(data_root)
    assert target == data_root / "tls" / "cacert.pem"
    assert target.read_text("utf-8") == "test certificate bundle"
    assert os.environ["SSL_CERT_FILE"] == str(target)

    source.unlink()
    assert prepare_persistent_ca_bundle(data_root) == target
    assert target.read_text("utf-8") == "test certificate bundle"


def test_persistent_ca_bundle_respects_valid_explicit_configuration(tmp_path, monkeypatch):
    configured = tmp_path / "custom-ca.pem"
    configured.write_text("custom", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(configured))

    assert prepare_persistent_ca_bundle(tmp_path / "app-data") == configured
