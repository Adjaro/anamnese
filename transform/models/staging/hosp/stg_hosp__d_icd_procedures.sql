with

d_icd_procedures as (
    select * from {{ source('hosp', 'd_icd_procedures') }}
)

select
    icd_code
    , icd_version
    , long_title
from d_icd_procedures
