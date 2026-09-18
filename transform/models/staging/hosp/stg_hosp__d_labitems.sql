with

d_labitems as (
    select * from {{ source('hosp', 'd_labitems') }}
)

select
    itemid
    , label
    , fluid
    , category
from d_labitems
