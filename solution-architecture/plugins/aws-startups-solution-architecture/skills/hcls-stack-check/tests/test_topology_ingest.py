"""Pytest for topology_ingest + region_lookup."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import region_lookup  # noqa: E402
import topology_ingest  # noqa: E402


def test_sample_fixture_exists():
    assert topology_ingest.SAMPLE_FIXTURE.exists()


def test_region_classification_eu_and_non_eu():
    assert region_lookup.is_eu_region("eu-west-1") is True
    assert region_lookup.is_eu_region("us-east-1") is False


def test_normalize_returns_three_resources():
    topo = topology_ingest.load_topology(topology_ingest.SAMPLE_FIXTURE)
    result = topology_ingest.normalize(topo)
    assert len(result.resources) == 3
