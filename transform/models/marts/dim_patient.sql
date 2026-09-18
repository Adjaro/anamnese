with

patients as (
    select * from {{ ref('stg_hosp__patients') }}
)

, admissions as (
    select * from {{ ref('stg_hosp__admissions') }}
)

, icustays as (
    select * from {{ ref('stg_icu__icustays') }}
)

, sejours_par_patient as (
    select
        subject_id
        , count(*) as nb_sejours
        , min(admittime) as premiere_admission_at
        , max(dischtime) as derniere_sortie_at
    from admissions
    group by subject_id
)

, sejours_icu_par_patient as (
    select
        subject_id
        , count(*) as nb_sejours_icu
    from icustays
    group by subject_id
)

select
    patients.subject_id
    , patients.gender as sexe
    , patients.anchor_age as age_ancrage_annees
    , patients.anchor_year as annee_ancrage
    , patients.anchor_year_group as periode_reelle_ancrage
    , patients.dod as deces_date
    , patients.dod is not null as est_decede
    , sejours_par_patient.premiere_admission_at
    , sejours_par_patient.derniere_sortie_at
    , coalesce(sejours_par_patient.nb_sejours, 0) as nb_sejours
    , coalesce(sejours_icu_par_patient.nb_sejours_icu, 0) as nb_sejours_icu
from patients
left join sejours_par_patient
    on patients.subject_id = sejours_par_patient.subject_id
left join sejours_icu_par_patient
    on patients.subject_id = sejours_icu_par_patient.subject_id
