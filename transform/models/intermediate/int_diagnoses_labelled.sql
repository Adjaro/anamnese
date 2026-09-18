with

diagnoses_icd as (
    select * from {{ ref('stg_hosp__diagnoses_icd') }}
)

, d_icd_diagnoses as (
    select * from {{ ref('stg_hosp__d_icd_diagnoses') }}
)

select
    diagnoses_icd.subject_id
    , diagnoses_icd.hadm_id
    , diagnoses_icd.seq_num
    , diagnoses_icd.icd_code
    , diagnoses_icd.icd_version
    , d_icd_diagnoses.long_title
from diagnoses_icd
-- Jointure sur le couple (code, version) : un meme icd_code existe en CIM-9 et en CIM-10.
left join d_icd_diagnoses
    on
        diagnoses_icd.icd_code = d_icd_diagnoses.icd_code
        and diagnoses_icd.icd_version = d_icd_diagnoses.icd_version
