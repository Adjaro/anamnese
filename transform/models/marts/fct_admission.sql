with

admissions as (
    select * from {{ ref('stg_hosp__admissions') }}
)

, patients as (
    select * from {{ ref('stg_hosp__patients') }}
)

, diagnostics as (
    select * from {{ ref('int_diagnoses_labelled') }}
)

, icustays as (
    select * from {{ ref('stg_icu__icustays') }}
)

, diagnostic_principal as (
    select
        hadm_id
        , icd_code
        , icd_version
        , long_title
    from diagnostics
    where seq_num = 1
)

, diagnostics_par_sejour as (
    select
        hadm_id
        , count(*) as nb_diagnostics
    from diagnostics
    group by hadm_id
)

, sejours_icu_par_admission as (
    select
        hadm_id
        , count(*) as nb_sejours_icu
    from icustays
    group by hadm_id
)

select
    admissions.hadm_id
    , admissions.subject_id
    , patients.gender as sexe
    , patients.anchor_age
    + year(admissions.admittime)
    - patients.anchor_year as age_admission_annees
    , row_number() over (
        partition by admissions.subject_id
        order by admissions.admittime, admissions.hadm_id
    ) as rang_admission
    , admissions.admittime as admission_at
    , admissions.dischtime as sortie_at
    , admissions.deathtime as deces_at
    , admissions.edregtime as urgences_entree_at
    , admissions.edouttime as urgences_sortie_at
    , round(epoch(admissions.dischtime - admissions.admittime) / 3600, 2) as duree_sejour_heures
    , round(epoch(admissions.dischtime - admissions.admittime) / 86400, 2) as duree_sejour_jours
    , admissions.admission_type as type_admission
    , admissions.admission_location as provenance
    , admissions.discharge_location as destination_sortie
    , admissions.insurance as assurance
    , admissions.language as langue
    , admissions.marital_status as statut_marital
    , admissions.race as origine_ethnique
    , admissions.hospital_expire_flag = 1 as est_deces_hospitalier
    , diagnostic_principal.icd_code as diagnostic_principal_code
    , diagnostic_principal.icd_version as diagnostic_principal_version_cim
    , diagnostic_principal.long_title as diagnostic_principal_libelle
    , coalesce(diagnostics_par_sejour.nb_diagnostics, 0) as nb_diagnostics
    , coalesce(sejours_icu_par_admission.nb_sejours_icu, 0) as nb_sejours_icu
    , sejours_icu_par_admission.hadm_id is not null as a_sejour_icu
from admissions
inner join patients
    on admissions.subject_id = patients.subject_id
left join diagnostic_principal
    on admissions.hadm_id = diagnostic_principal.hadm_id
left join diagnostics_par_sejour
    on admissions.hadm_id = diagnostics_par_sejour.hadm_id
left join sejours_icu_par_admission
    on admissions.hadm_id = sejours_icu_par_admission.hadm_id
