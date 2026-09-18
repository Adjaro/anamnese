with

diagnoses_icd as (
    select * from {{ source('hosp', 'diagnoses_icd') }}
)

select
    subject_id
    , hadm_id
    , seq_num
    , icd_code
    , icd_version
from diagnoses_icd
