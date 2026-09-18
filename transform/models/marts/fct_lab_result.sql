with

resultats as (
    select * from {{ ref('int_lab_results_labelled') }}
)

select
    labevent_id
    , subject_id
    , hadm_id
    , specimen_id
    , itemid
    , label as analyse_libelle
    , fluid as liquide_biologique
    , category as categorie_analyse
    , charttime as prelevement_at
    , value as valeur_texte
    , valuenum as valeur_num
    , valueuom as unite
    , ref_range_lower as borne_normale_basse_num
    , ref_range_upper as borne_normale_haute_num
    , coalesce(flag = 'abnormal', false) as est_anormal
    , priority as priorite
from resultats
