with

admissions as (
    select * from {{ source('hosp', 'admissions') }}
)

select
    subject_id
    , hadm_id
    , admittime
    , dischtime
    , deathtime
    , admission_type
    , admit_provider_id
    , admission_location
    , discharge_location
    , insurance
    , language
    , marital_status
    , race
    , edregtime
    , edouttime
    , hospital_expire_flag
from admissions
