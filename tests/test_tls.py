import hashlib
import ssl
from pathlib import Path

import pytest

from app.core.tls import tinvest_ssl_context

BUNDLE = Path(__file__).resolve().parents[1] / "certs" / "tinvest-ca.pem"
ROOT_SHA256 = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


def fingerprints(context: ssl.SSLContext) -> set[str]:
    return {hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)}


def test_extra_ca_is_scoped_and_keeps_standard_trust_and_hostname_verification():
    before = ssl.create_default_context()
    custom = tinvest_ssl_context(BUNDLE)
    after = ssl.create_default_context()
    assert ROOT_SHA256 in fingerprints(custom)
    assert fingerprints(before).issubset(fingerprints(custom))
    assert fingerprints(before) == fingerprints(after)
    assert custom.verify_mode == ssl.CERT_REQUIRED
    assert custom.check_hostname


def test_default_context_does_not_add_project_ca():
    baseline = ssl.create_default_context()
    result = tinvest_ssl_context(None)
    assert fingerprints(result) == fingerprints(baseline)
    assert result.verify_mode == ssl.CERT_REQUIRED
    assert result.check_hostname


def test_invalid_ca_bundle_fails_closed(tmp_path):
    invalid = tmp_path / "invalid.pem"
    invalid.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        tinvest_ssl_context(invalid)
