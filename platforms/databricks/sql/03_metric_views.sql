-- OSI governed metrics as Unity Catalog Metric Views (DESIGN §4 layer 3, ADR 0008/0011/0014, story 11).
-- Platform-native translation of semantic/metrics.yaml + semantic/semantic_models.yaml. Every measure/
-- dimension expr maps onto a canonical OSDU PDM column (ADR 0010). Read measures with MEASURE() in queries.
--
-- Cross-fact ratios (production_efficiency, oil_shortfall) live in `surveillance`, whose source unions
-- the actual (WELL_VOL_DAILY) and Well-kind forecast (PRODUCT_VOLUME_SUMMARY) oil per well/day — mirroring
-- compile.compute_surveillance. Compile-assembled KPIs (deferred volume, decline-vs-forecast, days-since-
-- test, allocation variance, period-over-period Δ, GOR-change) are NOT metric views (see README / ADRs).

-- Theme 1 (hero): production surveillance -----------------------------------------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.surveillance
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI production-surveillance signal (theme 1, hero): governed cross-fact metrics production_efficiency (actual/expected) and oil_shortfall (expected-actual) per well/day. Source unions the WELL_VOL_DAILY actual and the Well-kind forecast oil, mirroring compile.compute_surveillance.'
AS $$
version: 0.1
source: |
  WITH fc AS (
    SELECT re.ASSOCIATED_OBJECT_ID AS well_id, CAST(p.START_DATE AS DATE) AS d,
           p.VOLUME AS expected_oil
    FROM oag_fable.canonical.product_volume_summary p
    JOIN oag_fable.canonical.reporting_entity re
      ON p.REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID
     AND re.REPORTING_ENTITY_KIND = 'Well'
    WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast'
  )
  -- Actuals only for wells that have a forecast baseline, matching compile.compute_surveillance's
  -- evaluated set (expected e JOIN well LEFT JOIN actual); a producing well with no forecast is
  -- excluded rather than emitting a spurious negative oil_shortfall.
  SELECT v.WELL_ID AS well_id, CAST(v.VOLUME_DATE AS DATE) AS d,
         v.OIL_VOLUME AS actual_oil, 0.0 AS expected_oil
  FROM oag_fable.canonical.well_vol_daily v
  WHERE v.WELL_ID IN (SELECT DISTINCT well_id FROM fc)
  UNION ALL
  SELECT well_id, d, 0.0 AS actual_oil, expected_oil FROM fc
joins:
  - name: well
    source: oag_fable.canonical.well
    "on": source.well_id = well.WELL_ID
dimensions:
  - name: volume_date
    expr: d
  - name: well_id
    expr: well_id
  - name: uwi
    expr: well.UWI
  - name: field_id
    expr: well.FIELD_ID
  - name: operator
    expr: well.OPERATOR
measures:
  - name: actual_oil
    expr: SUM(actual_oil)
  - name: expected_oil
    expr: SUM(expected_oil)
  - name: production_efficiency
    expr: SUM(actual_oil) / NULLIF(SUM(expected_oil), 0)
  - name: oil_shortfall
    expr: SUM(expected_oil) - SUM(actual_oil)
$$;

-- Actuals fact: volumes, rates, uptime, water cut, GOR (themes 1/2/3/5/6) --------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.well_volumes
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI actuals fact (WELL_VOL_DAILY): governed volume/rate/uptime measures over the Well->Facility->Field hierarchy. Mirrors semantic/semantic_models.yaml (well_vol_daily) + metrics.yaml.'
AS $$
version: 0.1
source: oag_fable.canonical.well_vol_daily
joins:
  - name: well
    source: oag_fable.canonical.well
    "on": source.WELL_ID = well.WELL_ID
dimensions:
  - name: volume_date
    expr: CAST(VOLUME_DATE AS DATE)
  - name: well_id
    expr: source.WELL_ID
  - name: uwi
    expr: source.UWI
  - name: well_name
    expr: well.WELL_NAME
  - name: operator
    expr: well.OPERATOR
  - name: field_id
    expr: well.FIELD_ID
  - name: field_name
    expr: well.FIELD_NAME
  - name: facility_id
    expr: well.FACILITY_ID
measures:
  - name: actual_oil
    expr: SUM(OIL_VOLUME)
  - name: actual_gas
    expr: SUM(GAS_VOLUME)
  - name: actual_water
    expr: SUM(WATER_VOLUME)
  - name: cumulative_oil
    expr: SUM(OIL_VOLUME)
  - name: on_stream_hours
    expr: SUM(HOURS_ON)
  - name: calendar_days
    expr: COUNT(1)
  - name: uptime_pct
    expr: SUM(HOURS_ON) / (COUNT(1) * 24) * 100
  - name: water_cut
    expr: SUM(WATER_VOLUME) / NULLIF(SUM(OIL_VOLUME) + SUM(WATER_VOLUME), 0)
  - name: gor
    expr: SUM(GAS_VOLUME) * 1000 / NULLIF(SUM(OIL_VOLUME), 0)
$$;

-- Forecast fact: expected (decline-curve) oil ------------------------------------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.forecast_volumes
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI forecast fact (PRODUCT_VOLUME_SUMMARY): expected (decline-curve) oil, resolved forecast->well through Well-kind REPORTING_ENTITY. Mirrors semantic_models.yaml (product_volume_summary + reporting_entity) + metrics.yaml (expected_oil).'
AS $$
version: 0.1
source: |
  -- expected_oil is the FORECAST OIL baseline: bake the same PRODUCT='Oil', QUANTITY_METHOD='Forecast',
  -- KIND='Well' guards compile.compute_surveillance/compute_decline enforce, so the measure stays
  -- correct once PRODUCT_VOLUME_SUMMARY/REPORTING_ENTITY carry other products/methods/kinds.
  SELECT p.START_DATE, p.PRODUCT, p.QUANTITY_METHOD, p.VOLUME,
         re.REPORTING_ENTITY_KIND, re.ASSOCIATED_OBJECT_ID AS well_id
  FROM oag_fable.canonical.product_volume_summary p
  JOIN oag_fable.canonical.reporting_entity re
    ON p.REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID
   AND re.REPORTING_ENTITY_KIND = 'Well'
  WHERE p.PRODUCT = 'Oil' AND p.QUANTITY_METHOD = 'Forecast'
joins:
  - name: well
    source: oag_fable.canonical.well
    "on": source.well_id = well.WELL_ID
dimensions:
  - name: forecast_date
    expr: CAST(START_DATE AS DATE)
  - name: product
    expr: PRODUCT
  - name: quantity_method
    expr: QUANTITY_METHOD
  - name: reporting_entity_kind
    expr: REPORTING_ENTITY_KIND
  - name: well_id
    expr: well_id
  - name: uwi
    expr: well.UWI
  - name: field_id
    expr: well.FIELD_ID
measures:
  - name: expected_oil
    expr: SUM(VOLUME)
$$;

-- Downtime fact: downtime hours by cause (theme 2) -------------------------------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.downtime
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI downtime fact (DOWN_TIME_EVENT): governed downtime_hours (sum event duration), grouped by EVENT_CATEGORY for top downtime causes (theme 2). Mirrors semantic_models.yaml (down_time_event) + metrics.yaml.'
AS $$
version: 0.1
source: oag_fable.canonical.down_time_event
dimensions:
  - name: event_date
    expr: CAST(START_DATE AS DATE)
  - name: event_category
    expr: EVENT_CATEGORY
  - name: reporting_entity_id
    expr: REPORTING_ENTITY_ID
measures:
  - name: downtime_hours
    expr: SUM(DURATION_HOURS)
  - name: n_events
    expr: COUNT(1)
$$;

-- Well-test fact (theme 4) -------------------------------------------------------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.well_tests
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI well-test fact (WELL_TEST): governed well_tests_recorded (count) and test_oil_rate (avg rate at test), by well/type/date (theme 4). MAX(test_date) feeds compile-assembled days-since-last-test. Mirrors semantic_models.yaml (well_test) + metrics.yaml.'
AS $$
version: 0.1
source: oag_fable.canonical.well_test
joins:
  - name: well
    source: oag_fable.canonical.well
    "on": source.WELL_ID = well.WELL_ID
dimensions:
  - name: test_date
    expr: CAST(TEST_DATE AS DATE)
  - name: test_type
    expr: TEST_TYPE
  - name: well_id
    expr: source.WELL_ID
  - name: uwi
    expr: source.UWI
  - name: field_id
    expr: well.FIELD_ID
measures:
  - name: well_tests_recorded
    expr: COUNT(1)
  - name: test_oil_rate
    expr: AVG(OIL_RATE)
  - name: last_test_date
    expr: MAX(CAST(TEST_DATE AS DATE))
$$;

-- Allocation fact (theme 4) ------------------------------------------------------------------------
CREATE OR REPLACE VIEW oag_fable.semantic.allocation
WITH METRICS
LANGUAGE YAML
COMMENT 'OSI allocation fact (RPEN_ALLOCATION_FACTOR): governed allocation_factor (share of field-measured volume apportioned to a well), resolved to->well through Well-kind REPORTING_ENTITY (theme 4). Mirrors semantic_models.yaml (rpen_allocation_factor) + metrics.yaml.'
AS $$
version: 0.1
source: |
  -- Scope to product Oil (matching compile.compute_welltest) so allocation_factor is not summed
  -- across products once RPEN_ALLOCATION_FACTOR carries gas/water factors.
  SELECT a.START_DATE, a.END_DATE, a.PRODUCT, a.ALLOCATION_FACTOR,
         re.ASSOCIATED_OBJECT_ID AS well_id
  FROM oag_fable.canonical.rpen_allocation_factor a
  JOIN oag_fable.canonical.reporting_entity re
    ON a.TO_REPORTING_ENTITY_ID = re.REPORTING_ENTITY_ID
   AND re.REPORTING_ENTITY_KIND = 'Well'
  WHERE a.PRODUCT = 'Oil'
joins:
  - name: well
    source: oag_fable.canonical.well
    "on": source.well_id = well.WELL_ID
dimensions:
  - name: alloc_start_date
    expr: CAST(START_DATE AS DATE)
  - name: alloc_end_date
    expr: CAST(END_DATE AS DATE)
  - name: product
    expr: PRODUCT
  - name: well_id
    expr: well_id
  - name: uwi
    expr: well.UWI
  - name: field_id
    expr: well.FIELD_ID
measures:
  - name: allocation_factor
    expr: SUM(ALLOCATION_FACTOR)
$$;
