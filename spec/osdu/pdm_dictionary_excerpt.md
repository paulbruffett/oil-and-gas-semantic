# OSDU PDM v1.0 — Data Dictionary excerpt (verbatim)

Columns copied verbatim from the OSDU PDM v1.0 Data Dictionary (Apache-2.0, retrieved 2026-07-03).
Only the columns this generator populates are listed; each source table has many more. `Nullable=Y`
means the column *is* nullable; `Key=P` marks the primary key.
Source: <https://osdu.pages.opengroup.org/platform/domain-data-mgmt-services/production/core/dspdm-services/PDM/1.0/data-model-usage-guide/Data-Dictionary.html>

> **Authority:** this file is human-readable provenance. The machine-readable
> [`pdm_profile.json`](./pdm_profile.json) is authoritative for what the generator emits (names,
> dtypes, reference values) and is the file the conformance test binds to. The reference-value
> examples quoted below are OSDU's own comment text, whose casing is *not* standardized across R_
> tables (e.g. `Forecast`, `DAY`, `measured`, `OIL`); the generator normalizes the values it emits
> to Title Case — see `pdm_profile.json` `reference_values` for the pinned set.

## FIELD
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| FIELD_ID | Y | INTEGER | P | | Unique internal identifier for a FIELD object |
| FIELD_NAME | Y | CHARACTER VARYING(100) | | | The name of the field. Must be unique among all fields. |
| FIELD_TYPE_NAME | N | CHARACTER VARYING(50) | | R_FIELD_TYPE | Contains the type of this field. |

*Operator is not a FIELD column in OSDU PDM (it is modelled via FIELD_ORG_UNIT → ORG_UNIT, or on WELL.OPERATOR). We carry operator on WELL.*

## WELL
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| WELL_ID | Y | INTEGER | P | | The identifier used as the primary key for this row. |
| UWI | Y | CHARACTER VARYING(50) | | | Unique well identifier. |
| WELL_NAME | Y | CHARACTER VARYING(50) | | | Name assigned to the well. |
| FIELD_ID | N | INTEGER | | FIELD | Unique identifier for the field. |
| FIELD_NAME | N | CHARACTER VARYING(100) | | FIELD | The name of the FIELD. |
| OPERATOR | N | CHARACTER VARYING(40) | | | The Business Associate representing the owners of the well. |
| X_COORDINATE | N | NUMERIC(18,9) | | | X of surface (longitude or projected X per CRS). |
| Y_COORDINATE | N | NUMERIC(18,9) | | | Y of surface (latitude or projected Y per CRS). |
| FACILITY_ID | N | INTEGER | | FACILITY | The battery/facility the well routes to (Well → Facility → Field, #8). Denormalized FK — PPDM links well↔facility via a junction; carried flat here (ADR 0021). |

## REPORTING_ENTITY
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| REPORTING_ENTITY_ID | Y | INTEGER | P | | Primary key of REPORTING ENTITY table. |
| R_REPORTING_ENTITY_KIND_ID / (kind) | Y | INTEGER / VARCHAR | | R_REPORTING_ENTITY_KIND | Kind of reporting entity (Well, Facility, Field, Completion). |
| ASSOCIATED_OBJECT_ID | Y | INTEGER | | | Reference id to the actual entity (e.g. WELL). Polymorphic, typed by kind. |
| ASSOCIATED_OBJECT_NAME | Y | CHARACTER VARYING(50) | | | Reference name to the actual entity. |

*We use column `REPORTING_ENTITY_KIND` (the value column of R_REPORTING_ENTITY_KIND) as a flat kind label. We emit `Well` rows (one per well) and, for allocation (issue #6), one `Field` row per field — the group measurement point that allocation factors flow **from**.*

## WELL_VOL_DAILY  (actual daily volumes)
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| WELL_VOLUME_DAILY_ID | Y | INTEGER | P | | Primary key. |
| WELL_ID | Y | INTEGER | | WELL | Well id. |
| UWI | Y | CHARACTER VARYING(50) | | WELL | Unique well identifier. |
| VOLUME_DATE | Y | TIMESTAMP | | | Effective/reporting date of this volume summary. |
| HOURS_ON | N | NUMERIC(14,4) | | | The hours on for the record in the reported time period. |
| OIL_VOLUME | N | NUMERIC(20,10) | | | The oil volume for the reporting period. |
| GAS_VOLUME | N | NUMERIC(20,10) | | | The gas volume for the reporting period. |
| WATER_VOLUME | N | NUMERIC(20,10) | | | The water volume for the reporting period. |
| VOLUME_METHOD | N | CHARACTER VARYING(40) | | | measured, prorated, engineering study, etc. |

## PRODUCT_VOLUME_SUMMARY  (forecast/expected series; QUANTITY_METHOD='Forecast')
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| PRODUCT_VOLUME_SUMMARY_ID | Y | INTEGER | P | | Row identifier. |
| REPORTING_ENTITY_ID | Y | INTEGER | | REPORTING_ENTITY | Reporting entity the data is reported against. |
| REPORTING_ENTITY_NAME | Y | CHARACTER VARYING(50) | | | Reference name of the entity (e.g. WELL). |
| START_DATE | Y | TIMESTAMP | | | Start date of volume reported against entity. |
| END_DATE | Y | TIMESTAMP | | | End date of volume reported against entity. |
| PERIOD_KIND | Y | CHARACTER VARYING(40) | | R_PERIOD_KIND | Reporting period type (DAY, MONTH, YEAR). |
| REPORTING_FLOW | Y | CHARACTER VARYING(40) | | R_REPORTING_FLOW | Reporting flow (production, injection, disposition). |
| PRODUCT | Y | CHARACTER VARYING(40) | | R_REPORTING_PRODUCT | Product for which the volume is reported (Oil, Gas, Water). |
| QUANTITY_METHOD | Y | CHARACTER VARYING(40) | | R_QUANTITY_METHOD | "Allocated, Allowed, Estimated, Target, Measured, Budget, **Forecast** Etc." |
| VOLUME | N | NUMERIC(14,4) | | | The volume of the product measured. |
| VOLUME_UOM | N | CHARACTER VARYING(40) | | | Measurement unit used for reported volume. |

## DOWN_TIME_EVENT  (downtime events; deferment use case, issue #4)
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| DOWN_TIME_EVENT_ID | Y | INTEGER | P | | The identifier of this row of data. |
| REPORTING_ENTITY_ID | Y | INTEGER | | REPORTING_ENTITY | A unique identifier distinguishing the entity the downtime is reported against. |
| EVENT_CATEGORY | N | CHARACTER VARYING(40) | | R_EVENT_CATEGORY | The activity or event category (we carry the downtime cause here). |
| START_DATE | N | TIMESTAMP | | | When the downtime event commenced. |
| END_DATE | N | TIMESTAMP | | | When the downtime event concluded. |
| DURATION_HOURS | N | NUMERIC(15,5) | | | Length of downtime measured in hours. |

*We omit the dictionary's `R_EVENT_CATEGORY_ID` / `EVENT_SUB_CATEGORY` / `REMARK` / `IS_ACTIVE` and audit columns, carrying only the flat `EVENT_CATEGORY` value (as we carry `REPORTING_ENTITY_KIND`). A generated event spans a single VOLUME_DATE (START_DATE = END_DATE); DURATION_HOURS ∈ (0, 24]. Deferred volume is computed against the forecast series rather than stored (the PDM `PROD_DOWN_TIME_VOLUME_LOSS` companion table is out of scope). See ADR 0017.*

## WELL_TEST  (periodic well tests; well-test/allocation use case, issue #6)
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| WELL_TEST_ID | Y | INTEGER | P | | Primary key of the well test. |
| WELL_ID | Y | INTEGER | | WELL | Well the test was run on (PPDM WELL_TEST is keyed to the well/UWI). |
| UWI | Y | CHARACTER VARYING(50) | | WELL | Unique well identifier. |
| TEST_DATE | N | TIMESTAMP | | | Date the test was run. |
| TEST_TYPE | N | CHARACTER VARYING(40) | | R_TEST_TYPE | Kind of test (production, injectivity, buildup, …). |
| DURATION_HOURS | N | NUMERIC(15,5) | | | Length of the test in hours. |
| OIL_RATE | N | NUMERIC(20,10) | | | Oil rate measured at test. |
| OIL_RATE_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit of measure for OIL_RATE (per-value OUOM). |
| GAS_RATE | N | NUMERIC(20,10) | | | Gas rate measured at test. |
| GAS_RATE_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit of measure for GAS_RATE. |
| WATER_RATE | N | NUMERIC(20,10) | | | Water rate measured at test. |
| WATER_RATE_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit of measure for WATER_RATE. |

*`WELL_TEST` is the OSDU PDM (PPDM-3.9-based) well-test table — verified present in the OSDU PDM v1.0 Well-Test data model (2026-07); PPDM is cited as lineage only (ADR 0010). The OSDU PDM stores each measured value with its own OUOM column (`R_UOM`), which we honour per rate. In the OSDU model the rates live in child tables (`WELL_TEST_FLOW_PERIOD` / `WELL_TEST_FLOW_MEASUREMENT` / `WELL_TEST_MEASUREMENT`); we **denormalize** the oil/gas/water test rates onto `WELL_TEST`. `TEST_TYPE` is the flat `R_WELL_TEST_TYPE` value column (carried like `REPORTING_ENTITY_KIND`). A generated test spans a single TEST_DATE; validation/choke/pressure, run-number and audit columns are omitted. Test rates are the well's metered daily volumes on the test date. WKS analogue: `work-product-component--FlowTest`. See ADR 0019.*

## RPEN_ALLOCATION_FACTOR  (production allocation factors; allocation use case, issue #6)
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| RPEN_ALLOCATION_FACTOR_ID | Y | INTEGER | P | | Primary key of the allocation factor. |
| FROM_REPORTING_ENTITY_ID | Y | INTEGER | | REPORTING_ENTITY | Source (from) RPEN — the group/field measurement point being apportioned. |
| TO_REPORTING_ENTITY_ID | Y | INTEGER | | REPORTING_ENTITY | Target (to) RPEN — the well receiving the allocated share. |
| START_DATE | Y | TIMESTAMP | | | Start of the effective allocation period. |
| END_DATE | Y | TIMESTAMP | | | End of the effective allocation period. |
| PRODUCT | Y | CHARACTER VARYING(40) | | R_REPORTING_PRODUCT | Product the factor applies to (Oil, Gas, Water). |
| ALLOCATION_FACTOR | N | NUMERIC(14,10) | | | The apportioning factor (share of the from-entity's volume). |
| ALLOCATION_FACTOR_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit for the factor (dimensionless 'fraction'). |

*`RPEN_ALLOCATION_FACTOR` is the OSDU PDM (PPDM-3.9-based) production allocation-factor table — the OSDU-published table that records each **from RPEN** and **to RPEN** and the allocation factor (verified against the OSDU PDM v1.0 Volume-Relevant + Well-Test data-model pages, 2026-07). `RPEN` = `REPORTING_ENTITY`, so it is natively a **from-entity → to-entity factor**, and the PPDM-3.9 analogue `PDEN_ALLOC_FACTOR` is lineage only (ADR 0010). It is deliberately **not** a stored allocated-volume table (`PDEN_VOL_ALLOC` is out of scope — allocated volume is computed as `from-measured × factor`, so it can never drift). We carry flat `FROM_/TO_REPORTING_ENTITY_ID` keys (from = a Field-kind row, to = a Well-kind row); qualifier/method and audit columns are omitted. The factor carries its own per-value OUOM. Exact OSDU column spellings were not machine-verified from the deep dictionary page, so the columns above are a deliberate ADR-0019 profile selection. See ADR 0019.*

## FACILITY  (surface-facility master + asset hierarchy; asset-rollups use case, issue #8)
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| FACILITY_ID | Y | INTEGER | P | | Primary key of the facility (with FACILITY_TYPE — the PPDM FACILITY PK is the pair). |
| FACILITY_TYPE | Y | CHARACTER VARYING(40) | P | R_FACILITY_TYPE | Kind of facility; a battery is a FACILITY_TYPE value ('Battery'), not its own table. |
| FACILITY_NAME | N | CHARACTER VARYING(100) | | | Name assigned to the facility. |
| FIELD_ID | N | INTEGER | | FIELD | Field the facility belongs to. |
| OPERATOR | N | CHARACTER VARYING(40) | | | The Business Associate operating the facility. |
| LATITUDE | N | NUMERIC(18,9) | | | Facility centroid latitude. |
| LATITUDE_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit of measure for LATITUDE (per-value OUOM: 'dega'). |
| LONGITUDE | N | NUMERIC(18,9) | | | Facility centroid longitude. |
| LONGITUDE_OUOM | N | CHARACTER VARYING(40) | | R_UOM | Unit of measure for LONGITUDE (per-value OUOM: 'dega'). |

*`FACILITY` is the OSDU PDM (PPDM-3.9-based) surface-facility master. Its **primary key is the pair `(FACILITY_ID, FACILITY_TYPE)`** — a **battery is a `FACILITY_TYPE` value** (`R_FACILITY_TYPE`), not a separate table, matching PPDM/`PDEN_FACILITY` practice (verified against the OSDU PDM v1.0 + PPDM 3.9 FACILITY model, 2026-07). It links to `FIELD` and `OPERATOR`, and carries a centroid latitude/longitude each with its **own per-value OUOM column** (`dega`, decimal degrees), honouring the OSDU per-value-OUOM pattern. The `WELL → FACILITY → FIELD` asset hierarchy is carried as a flat `FACILITY_ID` FK on `WELL`. **Deliberate simplifications** (ADR 0021): the well↔facility link is a flat FK (PPDM uses a junction); operator/field are flat columns; a facility carries a single centroid (no footprint) and no CRS; status/class/regulatory and audit columns are omitted. WKS analogue: `master-data--GenericFacility`. See ADR 0021.*
