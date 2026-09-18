with

d_items as (
    select * from {{ source('icu', 'd_items') }}
)

select
    itemid
    , label
    , abbreviation
    , linksto
    , category
    , unitname
    , param_type
    , lownormalvalue
    , highnormalvalue
from d_items
