"""Secondary OSDU JSON manifest export (issue #15, ADRs 0007/0031).

Drives the generator through its public seam (`generate_dataset`) and asserts on the written
OSDU JSON manifests: they exist alongside the Parquet, are stamped with the same config hash,
validate against the vendored OSDU PDM profile, are byte-identical across identical runs, and
represent the same records as the canonical Parquet (Parquet stays canonical, ADR 0007).
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from oag_generator import generate_dataset, osdu_manifest_paths, read_dataset_manifest

PROFILE = json.loads(
    (Path(__file__).resolve().parents[1] / "spec" / "osdu" / "pdm_profile.json").read_text()
)["tables"]

# Coarse profile type -> the Python types a JSON round-trip yields (see pdm_profile.json "_types").
# bool is excluded from int deliberately (JSON true/false would satisfy isinstance(int)).
_TYPE_OK = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool)),
    "string": lambda v: isinstance(v, str),
}

CANONICAL_TABLES = [
    "field", "well", "reporting_entity", "well_vol_daily", "product_volume_summary", "down_time_event",
    "well_test", "rpen_allocation_factor", "facility",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_emits_osdu_manifests_alongside_parquet(tmp_path, small_config):
    """Same run emits an OSDU JSON manifest per canonical table, next to the Parquet."""
    m = generate_dataset(small_config, tmp_path)
    paths = osdu_manifest_paths(tmp_path)
    assert set(paths) == set(CANONICAL_TABLES)
    for key in CANONICAL_TABLES:
        assert paths[key].exists(), f"missing OSDU manifest {key}"
        manifest = _load(paths[key])
        assert manifest["osdu_table"] == PROFILE[key]["osdu_table"]
        assert manifest["record_count"] == len(manifest["records"])
        # The Parquet is canonical; the manifest is a secondary view of the same rows.
        assert manifest["record_count"] == pq.read_table(m.tables[key]).num_rows


def test_manifests_stamped_with_config_hash(tmp_path, small_config):
    """Every manifest carries the dataset's config hash, so a manifest set is provably comparable."""
    m = generate_dataset(small_config, tmp_path)
    for path in osdu_manifest_paths(tmp_path).values():
        assert _load(path)["config_hash"] == m.config_hash


def test_manifests_validate_against_pdm_profile(tmp_path, small_config):
    """Each record's data block has exactly the OSDU PDM columns, right types, allowed ref values."""
    generate_dataset(small_config, tmp_path)
    for key, path in osdu_manifest_paths(tmp_path).items():
        entry = PROFILE[key]
        names = [c["name"] for c in entry["columns"]]
        ref = entry.get("reference_values", {})
        manifest = _load(path)
        # The record envelope is OSDU-shaped: id + kind + data.
        assert manifest["kind"] == manifest["records"][0]["kind"] if manifest["records"] else True
        for rec in manifest["records"]:
            assert set(rec) == {"id", "kind", "data"}
            data = rec["data"]
            assert list(data) == names, f"{key} record columns diverged from profile"
            for col in entry["columns"]:
                assert _TYPE_OK[col["type"]](data[col["name"]]), (
                    f"{key}.{col['name']} type diverged in manifest"
                )
            for column, allowed in ref.items():
                assert data[column] in allowed, (
                    f"{key}.{column} emitted non-conformant value {data[column]!r}"
                )


def test_manifests_byte_identical_across_runs(tmp_path, small_config):
    """Same seed + config -> byte-identical manifests (determinism, ADR 0007)."""
    generate_dataset(small_config, tmp_path / "a")
    generate_dataset(small_config, tmp_path / "b")
    a = osdu_manifest_paths(tmp_path / "a")
    b = osdu_manifest_paths(tmp_path / "b")
    assert set(a) == set(b)
    for key in a:
        assert a[key].read_bytes() == b[key].read_bytes(), (
            f"OSDU manifest {key} is not byte-stable across identical runs"
        )


def test_parquet_and_json_represent_the_same_records(tmp_path, welltest_config):
    """Spot-check: manifest data blocks equal the canonical Parquet rows, cell-for-cell.

    Uses the fuller well-test config so tables with real signal (well tests, allocation) are covered,
    not only the tiny default.
    """
    m = generate_dataset(welltest_config, tmp_path)
    for key in CANONICAL_TABLES:
        table = pq.read_table(m.tables[key]).to_pydict()
        names = list(table)
        n = len(next(iter(table.values())))
        records = _load(osdu_manifest_paths(tmp_path)[key])["records"]
        assert len(records) == n, f"{key} record count diverged from Parquet"
        for i, rec in enumerate(records):
            assert rec["data"] == {name: table[name][i] for name in names}, (
                f"{key} row {i} diverged between Parquet and JSON"
            )


def test_dataset_json_indexes_the_osdu_manifests(tmp_path, small_config):
    """dataset.json exposes the osdu manifests as a flat key -> relative-path map (like gold)."""
    generate_dataset(small_config, tmp_path)
    manifest = read_dataset_manifest(tmp_path)
    assert set(manifest["osdu"]) == set(CANONICAL_TABLES)
    assert (tmp_path / manifest["osdu"]["well"]).exists()


def test_osdu_manifest_paths_absent_returns_empty(tmp_path, small_config):
    """A dataset.json without an osdu index (e.g. the redacted eval bundle) yields {}, not KeyError."""
    generate_dataset(small_config, tmp_path)
    manifest = read_dataset_manifest(tmp_path)
    del manifest["osdu"]
    (tmp_path / "dataset.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    assert osdu_manifest_paths(tmp_path) == {}
