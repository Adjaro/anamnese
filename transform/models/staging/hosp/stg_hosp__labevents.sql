with

labevents as (
    select * from {{ source('hosp', 'labevents') }}
)

select
    labevent_id
    , subject_id
    , hadm_id
    , specimen_id
    , itemid
    , order_provider_id
    , charttime
    , storetime
    , value
    , valuenum
    , valueuom
    , ref_range_lower
    , ref_range_upper
    , flag
    , priority
    , comments
from labevents
