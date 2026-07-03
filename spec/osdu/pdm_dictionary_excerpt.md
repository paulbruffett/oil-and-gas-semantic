# OSDU PDM v1.0 — Data Dictionary excerpt (verbatim)

Columns copied verbatim from the OSDU PDM v1.0 Data Dictionary (Apache-2.0, retrieved 2026-07-03).
Only the columns this generator populates are listed; each source table has many more. `Nullable=Y`
means the column *is* nullable; `Key=P` marks the primary key.
Source: <https://osdu.pages.opengroup.org/platform/domain-data-mgmt-services/production/core/dspdm-services/PDM/1.0/data-model-usage-guide/Data-Dictionary.html>

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

## REPORTING_ENTITY
| Column | Nullable | Type | Key | Ref | Comment |
|---|---|---|---|---|---|
| REPORTING_ENTITY_ID | Y | INTEGER | P | | Primary key of REPORTING ENTITY table. |
| R_REPORTING_ENTITY_KIND_ID / (kind) | Y | INTEGER / VARCHAR | | R_REPORTING_ENTITY_KIND | Kind of reporting entity (Well, Facility, Field, Completion). |
| ASSOCIATED_OBJECT_ID | Y | INTEGER | | | Reference id to the actual entity (e.g. WELL). Polymorphic, typed by kind. |
| ASSOCIATED_OBJECT_NAME | Y | CHARACTER VARYING(50) | | | Reference name to the actual entity. |

*We use column `REPORTING_ENTITY_KIND` (the value column of R_REPORTING_ENTITY_KIND) as a flat kind label.*

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
