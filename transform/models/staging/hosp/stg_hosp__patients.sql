with

patients as (
    select * from {{ source('hosp', 'patients') }}
)

select
    subject_id
    , gender
    , anchor_age
    , anchor_year
    , anchor_year_group
    , dod
from patients
