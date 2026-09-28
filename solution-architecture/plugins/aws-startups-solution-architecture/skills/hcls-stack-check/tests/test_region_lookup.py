"""Tests for region_lookup: pure slug normalizer + unknown handling. Offline.

The live SSM call is network-marked and skipped in offline runs.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import region_lookup as rl  # noqa: E402


def test_is_eu_region():
    assert rl.is_eu_region("eu-west-1") is True
    assert rl.is_eu_region("us-east-1") is False


def test_resolve_service_slug_known_and_prefixes():
    assert rl.resolve_service_slug("Amazon S3") == "s3"
    assert rl.resolve_service_slug("AWS Lambda") == "lambda"
    assert rl.resolve_service_slug("Amazon Comprehend Medical") == "comprehendmedical"
    assert rl.resolve_service_slug("Braket") == "braket"


def test_resolve_service_slug_empty_is_none():
    assert rl.resolve_service_slug("") is None
    assert rl.resolve_service_slug("   ") is None


def test_service_offers_eu_region_unknown_when_slug_unresolvable():
    res = rl.service_offers_eu_region("")
    assert res["offers_eu"] is None
    assert res["eu_regions_offered"] == []
    assert res["resolved_slug"] is None


def test_service_offers_eu_region_unknown_on_boto_error(monkeypatch):
    # Simulate boto3 raising (auth/permission/network): must return unknown.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise RuntimeError("simulated credential error")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = rl.service_offers_eu_region("Amazon S3")
    assert res["offers_eu"] is None
    assert res["resolved_slug"] == "s3"


@pytest.mark.network
@pytest.mark.skip(reason="network + AWS creds, run explicitly with valid AWS credentials")
def test_service_offers_eu_region_live():
    res = rl.service_offers_eu_region("Amazon S3")
    assert res["offers_eu"] is True
    assert res["resolved_slug"] == "s3"
