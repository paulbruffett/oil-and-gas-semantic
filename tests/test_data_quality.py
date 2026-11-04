"""Engineering tests for the data-quality scenario knob (ADR 0039).

Driven through the public seams: ``generate_dataset`` output (Parquet + gold + dataset.json), the
reference compile, and the gold check. Off, the knob must leave every dataset byte-identical; on, the
defects must be in the written data, every KPI gold must be the cleaned recomputation, the compile
must reproduce it, and the findings gold must be non-empty on any seed.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from oag_generator import config_hash, generate_dataset, load_config, schema
from oag_generator.data_quality import (
    BBL_PER_M3,
    DEFECT_CORRECTION,
    DEFECT_DUPLICATE,
    DEFECT_FLATLINE,
    DEFECT_UNIT_ERROR,
    clean_well_vol_daily,
)
from oag_generator.gold import (
    compute_decline_gold,
    compute_deferment_gold,
    compute_rollup_gold,
    compute_surveillance_gold,
    compute_watchlist_gold,
    compute_welltest_gold,
)
from oag_generator.questions import DATA_QUALITY_QUESTION_ID, load_catalog
from oag_semantic.compile import (
    compute_decline,
    compute_deferment,
    compute_rollup,
    compute_surveillance,
    compute_watchlist,
    compute_welltest,
)
from oag_semantic.grading import SPECS, grade_answer, score_submissions

REPO = Path(__file__).resolve().parents[1]
DQ_CONFIG = REPO / "configs" / "data-quality.yaml"

KNOB = {
    "duplicate_count": 2,
    "correction_count": 2,
    "flatline_count": 1,
    "flatline_days": 4,
    "unit_error_count": 2,
}

# gold artifact stem -> (gold function, reference-compile function)
THEMES = {
    "surveillance": (compute_surveillance_gold, compute_surveillance),
    "deferment": (compute_deferment_gold, compute_deferment),
    "decline": (compute_decline_gold, compute_decline),
    "welltest": (compute_welltest_gold, compute_welltest),
    "watchlist": (compute_watchlist_gold, compute_watchlist),
    "rollups": (compute_rollup_gold, compute_rollup),
}


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _raw_cols(dataset_dir: Path) -> dict[str, dict[str, list]]:
    return {
        spec.key: pq.read_table(dataset_dir / "canonical" / f"{spec.key}.parquet").to_pydict()
        for spec in schema.TABLES
    }


def _gold(dataset_dir: Path, stem: str) -> dict:
    return json.loads((dataset_dir / "gold" / f"{stem}.json").read_text())


@pytest.fixture(scope="module")
def dq_dataset(tmp_path_factory) -> Path:
    """The shipped knob-on config (the reference config + data_quality)."""
    out = tmp_path_factory.mktemp("dq_dataset")
    generate_dataset(DQ_CONFIG, out)
    return out


# --- knob off: nothing changes ----------------------------------------------------------------


def test_knob_off_is_byte_identical(tmp_path, small_config):
    """An all-zero data_quality block, even with non-default parameters, writes exactly the files the
    config without the block writes, under the same config_hash (ADR 0039)."""
    off = {**small_config, "data_quality": {"flatline_days": 9, "anchor_well_id": 99}}
    a = generate_dataset(small_config, tmp_path / "a")
    b = generate_dataset(off, tmp_path / "b")
    assert a.config_hash == b.config_hash == config_hash(load_config(small_config))
    assert _tree(tmp_path / "a") == _tree(tmp_path / "b")
    manifest = json.loads((tmp_path / "a" / "dataset.json").read_text())
    assert "data_quality" not in manifest["config"]
    assert "data_quality" not in manifest["gold"]


def test_reference_config_hash_is_unchanged():
    assert config_hash(load_config(REPO / "configs" / "reference.yaml")) == "12a110eecfe2"
    assert config_hash(load_config(REPO / "configs" / "default.yaml")) == config_hash(load_config({}))


def test_knob_on_changes_only_well_vol_daily(tmp_path, small_config):
    """Defects come from their own rng stream after every table is built: the other eight canonical
    tables are byte-identical to the knob-off run."""
    a = generate_dataset(small_config, tmp_path / "off")
    b = generate_dataset({**small_config, "data_quality": KNOB}, tmp_path / "on")
    assert a.config_hash != b.config_hash
    for spec in schema.TABLES:
        same = a.tables[spec.key].read_bytes() == b.tables[spec.key].read_bytes()
        assert same == (spec.key != schema.WELL_VOL_DAILY.key), spec.key


# --- knob on: the defects are in the written data -----------------------------------------------


def test_defects_are_present_in_the_written_data(dq_dataset):
    wvd = _raw_cols(dq_dataset)[schema.WELL_VOL_DAILY.key]
    rows = {}
    for i, key in enumerate(zip(wvd["WELL_ID"], wvd["VOLUME_DATE"])):
        rows.setdefault(key, []).append(i)
    findings = _gold(dq_dataset, "data_quality")["findings"]
    assert {f["defect"] for f in findings} == {
        DEFECT_DUPLICATE, DEFECT_CORRECTION, DEFECT_FLATLINE, DEFECT_UNIT_ERROR
    }
    cfg = load_config(DQ_CONFIG)
    for f in findings:
        idx = rows[(f["well_id"], f["volume_date"])]
        methods = [wvd["VOLUME_METHOD"][i] for i in idx]
        oil = [wvd["OIL_VOLUME"][i] for i in idx]
        if f["defect"] == DEFECT_DUPLICATE:
            assert methods == ["Measured", "Measured"]
            assert len({tuple(wvd[c][i] for c in ("HOURS_ON", "OIL_VOLUME", "GAS_VOLUME",
                                                   "WATER_VOLUME")) for i in idx}) == 1
        elif f["defect"] == DEFECT_CORRECTION:
            assert methods == ["Measured", "Corrected"]
            assert oil[0] != oil[1] and oil[1] == f["oil_volume_kept"]
        elif f["defect"] == DEFECT_FLATLINE:
            (i,) = idx
            run = range(i, i + f["n_days"])
            assert f["n_days"] == cfg.data_quality["flatline_days"]
            assert all(wvd["HOURS_ON"][j] == 24.0 for j in run)
            assert len({(wvd["OIL_VOLUME"][j], wvd["GAS_VOLUME"][j], wvd["WATER_VOLUME"][j])
                        for j in run}) == 1
            assert wvd["VOLUME_DATE"][i + f["n_days"] - 1] == f["end_date"]
        else:  # unit error: oil is the m3 figure, so that day's GOR is ~6.29x the day before
            (i,) = idx
            gor_today = wvd["GAS_VOLUME"][i] / wvd["OIL_VOLUME"][i]
            gor_before = wvd["GAS_VOLUME"][i - 1] / wvd["OIL_VOLUME"][i - 1]
            assert gor_today / gor_before == pytest.approx(BBL_PER_M3, rel=0.25)


def test_osdu_profile_admits_the_corrected_method(dq_dataset):
    profile = json.loads((REPO / "spec" / "osdu" / "pdm_profile.json").read_text())["tables"]
    allowed = set(profile["well_vol_daily"]["reference_values"]["VOLUME_METHOD"])
    wvd = _raw_cols(dq_dataset)[schema.WELL_VOL_DAILY.key]
    assert set(wvd["VOLUME_METHOD"]) == {"Measured", "Corrected"} <= allowed


# --- gold is the cleaned recomputation ----------------------------------------------------------


def test_kpi_gold_is_the_cleaned_recomputation(dq_dataset):
    """Every theme's gold equals its gold function run on the cleaned rows read back from Parquet, and
    differs from the same function on the raw rows somewhere (the defects matter)."""
    cfg = load_config(DQ_CONFIG)
    raw = _raw_cols(dq_dataset)
    cleaned = {**raw, schema.WELL_VOL_DAILY.key: clean_well_vol_daily(raw[schema.WELL_VOL_DAILY.key])}
    differs = []
    for stem, (gold_fn, _) in THEMES.items():
        assert _gold(dq_dataset, stem) == json.loads(json.dumps(gold_fn(cleaned, cfg))), stem
        if gold_fn(raw, cfg) != gold_fn(cleaned, cfg):
            differs.append(stem)
    assert differs, "no theme's gold depends on the cleaning rules"


def test_cleaning_drops_exactly_the_removed_rows(dq_dataset):
    wvd = _raw_cols(dq_dataset)[schema.WELL_VOL_DAILY.key]
    cleaned = clean_well_vol_daily(wvd)
    gold = _gold(dq_dataset, "data_quality")
    assert len(wvd["WELL_ID"]) - len(cleaned["WELL_ID"]) == sum(f["rows_removed"] for f in gold["findings"])
    assert len(set(zip(cleaned["WELL_ID"], cleaned["VOLUME_DATE"]))) == len(cleaned["WELL_ID"])


def test_duplicates_and_corrections_clean_back_to_the_truth(tmp_path, small_config):
    """Cleaning undoes the defects it fixes: with only duplicates and corrections injected, the
    knob-off gold passes the gold check on the knob-on dataset."""
    from oag_semantic.grading import submission_from_gold

    off = generate_dataset(small_config, tmp_path / "off").output_dir
    knob = {"duplicate_count": 3, "correction_count": 3}
    on = generate_dataset({**small_config, "data_quality": knob}, tmp_path / "on").output_dir
    for q in load_catalog().questions():
        if q.scenario is None:
            spec = SPECS[q.gold_id]
            sub = submission_from_gold(json.loads((off / q.gold_artifact).read_text()), spec, q.id)
            grade = grade_answer(sub, json.loads((on / q.gold_artifact).read_text()), spec)
            assert grade.correct, grade.summary()


# --- the reference compile reproduces the cleaned gold ------------------------------------------


def _compiled_submission(result, spec, question_id) -> dict:
    def rows(value):
        return [dataclasses.asdict(r) if dataclasses.is_dataclass(r) else dict(r) for r in value]

    return {
        "question_id": question_id,
        "answer": "",
        "key_values": {s.set_key: rows(getattr(result, s.set_key)) for s in spec.sets()},
    }


def test_reference_compile_reproduces_gold_with_the_knob_on(dq_dataset):
    """The compile reads the cleaned well_vol_daily it builds in SQL and matches every theme's gold."""
    for q in load_catalog().questions():
        if q.scenario is not None:
            continue
        stem = Path(q.gold_artifact).stem
        spec = SPECS[q.gold_id]
        result = THEMES[stem][1](dq_dataset)
        grade = grade_answer(_compiled_submission(result, spec, q.id), _gold(dq_dataset, stem), spec)
        assert grade.correct, grade.summary()


# --- findings gold + the gold check -------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_findings_gold_is_non_empty_on_every_seed(tmp_path, small_config, seed):
    cfg = {**small_config, "seed": seed, "data_quality": KNOB}
    m = generate_dataset(cfg, tmp_path)
    gold = json.loads(m.gold["data_quality"].read_text())
    assert gold["question_id"] == DATA_QUALITY_QUESTION_ID
    expected = 1 + sum(KNOB[k] for k in KNOB if k.endswith("_count"))  # + the anchor duplicate
    assert gold["n_findings"] == len(gold["findings"]) == expected
    ids = [f["finding_id"] for f in gold["findings"]]
    assert len(ids) == len(set(ids))
    anchor = f"{load_config(cfg).data_quality['anchor_well_id']}:{small_config['end_date']}:duplicate"
    assert anchor in ids


def test_only_the_anchor_when_counts_are_one_type(tmp_path, small_config):
    """Any count turns the knob on, and the anchor duplicate always comes with it."""
    m = generate_dataset({**small_config, "data_quality": {"unit_error_count": 1}}, tmp_path)
    defects = sorted(f["defect"] for f in json.loads(m.gold["data_quality"].read_text())["findings"])
    assert defects == [DEFECT_DUPLICATE, DEFECT_UNIT_ERROR]


def test_gold_check_grades_the_findings_question_only_when_the_knob_is_on(
    dq_dataset, dataset_dir, build_oracle_submissions
):
    on = score_submissions(build_oracle_submissions(dq_dataset), dq_dataset)
    assert on.passed and on.n_graded == 7 and on.skipped == []
    off = score_submissions(build_oracle_submissions(dataset_dir), dataset_dir)
    assert off.passed and off.n_graded == 6 and off.skipped == []


def test_findings_gold_fails_a_wrong_action(dq_dataset, build_oracle_submissions):
    """Treating a flagged defect as fixed (rows_removed 1) fails the findings question."""
    subs = build_oracle_submissions(dq_dataset)
    findings = subs[DATA_QUALITY_QUESTION_ID]["key_values"]["findings"]
    flat = next(f for f in findings if f["finding_id"].endswith(DEFECT_FLATLINE))
    flat["rows_removed"] = 1
    report = score_submissions(subs, dq_dataset)
    (grade,) = [g for g in report.grades if g.question_id == DATA_QUALITY_QUESTION_ID]
    assert not grade.correct and grade.value_mismatches[0]["key"] == "rows_removed"


# --- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"duplicate_count": -1},
        {"unit_error_count": 1.5},
        {"flatline_days": 1},
        {"correction_bias_min": 0.0},
        {"correction_bias_min": 0.4, "correction_bias_max": 0.2},
        {"duplicate_count": 1, "anchor_well_id": 99},
    ],
)
def test_invalid_data_quality_blocks_are_rejected(small_config, block):
    with pytest.raises(ValueError, match="data_quality"):
        load_config({**small_config, "data_quality": block})
