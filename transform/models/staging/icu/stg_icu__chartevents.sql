with

chartevents as (
    select * from {{ source('icu', 'chartevents') }}
)

select
    subject_id
    , hadm_id
    , stay_id
    , caregiver_id
    , charttime
    , storetime
    , itemid
    , value
    , valuenum
    , valueuom
    , warning
from chartevents
