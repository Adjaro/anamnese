with

labevents as (
    select * from {{ ref('stg_hosp__labevents') }}
)

, d_labitems as (
    select * from {{ ref('stg_hosp__d_labitems') }}
)

select
    labevents.labevent_id
    , labevents.subject_id
    , labevents.hadm_id
    , labevents.specimen_id
    , labevents.itemid
    , d_labitems.label
    , d_labitems.fluid
    , d_labitems.category
    , labevents.charttime
    , labevents.value
    , labevents.valuenum
    , labevents.valueuom
    , labevents.ref_range_lower
    , labevents.ref_range_upper
    , labevents.flag
    , labevents.priority
from labevents
left join d_labitems
    on labevents.itemid = d_labitems.itemid
