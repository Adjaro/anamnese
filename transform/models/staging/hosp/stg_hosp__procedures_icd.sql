with

procedures_icd as (
    select * from {{ source('hosp', 'procedures_icd') }}
)

select
    subject_id
    , hadm_id
    , seq_num
    , chartdate
    , icd_code
    , icd_version
from procedures_icd
