with

icustays as (
    select * from {{ source('icu', 'icustays') }}
)

select
    subject_id
    , hadm_id
    , stay_id
    , first_careunit
    , last_careunit
    , intime
    , outtime
    , los
from icustays
