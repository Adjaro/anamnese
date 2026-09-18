with

icustays as (
    select * from {{ ref('stg_icu__icustays') }}
)

, admissions as (
    select * from {{ ref('stg_hosp__admissions') }}
)

, patients as (
    select * from {{ ref('stg_hosp__patients') }}
)

select
    icustays.stay_id
    , icustays.hadm_id
    , icustays.subject_id
    , patients.gender as sexe
    , patients.anchor_age
    + year(admissions.admittime)
    - patients.anchor_year as age_admission_annees
    , icustays.first_careunit as premiere_unite
    , icustays.last_careunit as derniere_unite
    , icustays.first_careunit != icustays.last_careunit as a_change_unite
    , icustays.intime as entree_icu_at
    , icustays.outtime as sortie_icu_at
    , round(epoch(icustays.outtime - icustays.intime) / 3600, 2) as duree_sejour_icu_heures
    , round(icustays.los, 2) as duree_sejour_icu_jours
    , round(
        epoch(icustays.intime - admissions.admittime) / 3600, 2
    ) as delai_admission_icu_heures
    , admissions.hospital_expire_flag = 1 as est_deces_hospitalier
from icustays
inner join admissions
    on icustays.hadm_id = admissions.hadm_id
inner join patients
    on icustays.subject_id = patients.subject_id
