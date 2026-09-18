with

d_icd_diagnoses as (
    select * from {{ source('hosp', 'd_icd_diagnoses') }}
)

select
    icd_code
    , icd_version
    , long_title
from d_icd_diagnoses
