with

diagnostics as (
    select * from {{ ref('int_diagnoses_labelled') }}
)

, admissions as (
    select * from {{ ref('fct_admission') }}
)

select
    diagnostics.hadm_id
    , diagnostics.seq_num as rang_diagnostic
    , diagnostics.subject_id
    , admissions.sexe
    , admissions.age_admission_annees
    , diagnostics.seq_num = 1 as est_diagnostic_principal
    , diagnostics.icd_code as code_cim
    , diagnostics.icd_version as version_cim
    -- Categorie CIM : 3 premiers caracteres, sauf les codes CIM-9 « E » (causes
    -- externes) dont la categorie en compte 4.
    , case
        when diagnostics.icd_version = 9 and diagnostics.icd_code like 'E%'
            then left(diagnostics.icd_code, 4)
        else left(diagnostics.icd_code, 3)
    end as categorie_cim
    , diagnostics.long_title as libelle
    , admissions.est_deces_hospitalier
from diagnostics
inner join admissions
    on diagnostics.hadm_id = admissions.hadm_id
