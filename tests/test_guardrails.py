"""Tests des garde-fous SQL : une regle de CLAUDE.md §3, au moins un test.

Chaque cas d'injection est ecrit comme le produirait un LLM manipule. Un test
qui passe au vert en acceptant une requete dangereuse est une faille.
"""

from __future__ import annotations

import pytest

from anamnese.guardrails import SQLValidationError, validate

pytestmark = pytest.mark.guardrails

TABLES = {"dim_patient", "fct_admission", "fct_icu_stay", "fct_lab_result"}


def _rejette(sql: str, motif: str) -> None:
    with pytest.raises(SQLValidationError, match=motif):
        validate(sql, TABLES)


# --------------------------------------------------------------------------- #
# Requetes legitimes : les garde-fous ne doivent pas bloquer le NL2SQL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "select count(*) from dim_patient",
        "select sexe, avg(duree_sejour_heures) from fct_admission group by sexe",
        "select * from fct_admission join dim_patient using (subject_id)",
        "select * from fct_admission where hadm_id in (select hadm_id from fct_icu_stay)",
        "select subject_id from fct_admission union select subject_id from fct_icu_stay",
        "select subject_id from fct_admission union all select subject_id from fct_icu_stay",
        "with longs as (select * from fct_admission where duree_sejour_jours > 7)"
        " select count(*) from longs",
        "with a as (select * from fct_admission), b as (select * from a) select * from b",
        "with recursive n as (select 1 as i union all select i + 1 from n where i < 3)"
        " select * from n",
        "select * from main.fct_admission",
        "select * from FCT_ADMISSION",
        "from dim_patient select count(*)",
        "select date_diff('hour', admission_at, sortie_at) from fct_admission",
        "select analyse_libelle from fct_lab_result where analyse_libelle ilike '%glucose%'",
    ],
)
def test_accepte_une_requete_de_lecture_sur_tables_autorisees(sql: str) -> None:
    assert validate(sql, TABLES)


def test_renvoie_le_sql_normalise_et_non_la_chaine_brute() -> None:
    brut = "SeLeCt   count(*)\n\n  FROM   dim_patient"
    normalise = validate(brut, TABLES)
    assert normalise != brut
    assert normalise == "SELECT COUNT(*) FROM dim_patient"


def test_retire_les_commentaires_du_sql_renvoye() -> None:
    normalise = validate("select * from dim_patient -- ; drop table dim_patient", TABLES)
    assert "drop" not in normalise.lower()
    assert "--" not in normalise
    assert "/*" not in normalise


def test_accepte_un_point_virgule_final_unique() -> None:
    assert validate("select 1 from dim_patient;", TABLES)


def test_liste_blanche_insensible_a_la_casse() -> None:
    assert validate("select * from dim_patient", {"DIM_PATIENT"})


# --------------------------------------------------------------------------- #
# Regle 1 : toute erreur de parsing est un rejet
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql", ["select from where", "select * from (", "selec * from dim_patient"]
)
def test_rejette_un_sql_non_parsable(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql, TABLES)


@pytest.mark.parametrize("sql", ["", "   ", ";", "-- seulement un commentaire"])
def test_rejette_une_requete_vide(sql: str) -> None:
    _rejette(sql, "vide")


# --------------------------------------------------------------------------- #
# Une seule instruction : injection par empilement
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "select * from dim_patient; drop table dim_patient",
        "select * from dim_patient; delete from dim_patient",
        "select * from dim_patient /* innocent */; attach 'autre.db'",
        "select 1 from dim_patient; select 2 from dim_patient",
    ],
)
def test_rejette_plusieurs_instructions_empilees(sql: str) -> None:
    _rejette(sql, "une seule requete")


# --------------------------------------------------------------------------- #
# Regle 2 : la racine est un SELECT ou un UNION
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "insert into dim_patient select * from dim_patient",
        "update dim_patient set sexe = 'M'",
        "delete from dim_patient",
        "drop table dim_patient",
        "create table copie as select * from dim_patient",
        "alter table dim_patient add column x int",
        "select * from dim_patient intersect select * from dim_patient",
        "select * from dim_patient except select * from dim_patient",
        "(select * from dim_patient)",
        "describe dim_patient",
        "pragma version",
        "set threads = 1",
    ],
)
def test_rejette_une_racine_qui_n_est_pas_select_ou_union(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql, TABLES)


# --------------------------------------------------------------------------- #
# Regle 3 : instructions d'ecriture ou de session interdites partout
# --------------------------------------------------------------------------- #
def test_rejette_un_delete_cache_dans_une_cte() -> None:
    _rejette(
        "with supprimes as (delete from dim_patient returning *) select * from supprimes",
        "interdite",
    )


def test_rejette_select_into_qui_cree_une_table() -> None:
    _rejette("select * into copie from dim_patient", "interdite")


@pytest.mark.parametrize(
    "sql",
    ["attach 'autre.db' as autre", "copy dim_patient to 'fuite.csv'", "install httpfs"],
)
def test_rejette_attach_copy_install(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql, TABLES)


def test_rejette_load_non_reconnu_par_sqlglot() -> None:
    # sqlglot ne connait pas LOAD et le replie en Command : rejete comme tel.
    with pytest.raises(SQLValidationError):
        validate("load httpfs", TABLES)


# --------------------------------------------------------------------------- #
# Regle 4 : liste blanche de tables, CTE declarees tolerees
# --------------------------------------------------------------------------- #
def test_rejette_une_table_hors_liste_blanche() -> None:
    _rejette("select * from stg_hosp__patients", "non autorisee : stg_hosp__patients")


def test_rejette_une_union_vers_une_table_hors_liste_blanche() -> None:
    _rejette(
        "select subject_id from dim_patient union select subject_id from stg_hosp__patients",
        "non autorisee",
    )


def test_rejette_une_jointure_vers_une_table_hors_liste_blanche() -> None:
    _rejette(
        "select * from dim_patient join stg_hosp__admissions using (subject_id)",
        "non autorisee",
    )


def test_rejette_une_sous_requete_vers_une_table_hors_liste_blanche() -> None:
    _rejette(
        "select * from dim_patient where subject_id in (select subject_id from secret)",
        "non autorisee : secret",
    )


def test_rejette_un_lateral_vers_une_table_hors_liste_blanche() -> None:
    _rejette("select * from dim_patient, lateral (select * from secret) s", "non autorisee")


def test_rejette_une_cte_masquant_un_nom_autorise() -> None:
    # La CTE porte un nom de la liste blanche mais lit une table interdite.
    _rejette(
        "with fct_admission as (select * from secret) select * from fct_admission",
        "non autorisee : secret",
    )


def test_rejette_une_cte_qui_se_lit_elle_meme_sans_recursive() -> None:
    # Sans RECURSIVE, `secret` dans le corps designe la vraie table, pas la CTE.
    _rejette("with secret as (select * from secret) select * from secret", "non autorisee")


def test_rejette_une_cte_qui_lit_une_cte_declaree_apres_elle() -> None:
    _rejette(
        "with a as (select * from secret), secret as (select 1) select * from a",
        "non autorisee",
    )


def test_rejette_une_cte_de_sous_requete_masquant_une_table_exterieure() -> None:
    # La CTE `secret` n'existe que dans la sous-requete ; le `secret` exterieur
    # est la vraie table.
    _rejette(
        "select * from (with secret as (select 1 as x) select * from secret) s1, secret",
        "non autorisee",
    )


def test_accepte_une_cte_de_nom_quelconque_sur_table_autorisee() -> None:
    assert validate("with secret as (select * from dim_patient) select * from secret", TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from information_schema.tables",
        "select * from pg_catalog.pg_tables",
        "select * from autre.dim_patient",
        "select * from anamnese.main.dim_patient",
        "select * from sqlite_master",
    ],
)
def test_rejette_les_schemas_et_catalogues_systeme(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql, TABLES)


def test_rejette_un_fichier_lu_par_remplacement_de_table() -> None:
    # DuckDB lit `from 'fichier.csv'` comme un fichier.
    _rejette("select * from '/etc/passwd'", "non autorisee")


def test_rejette_tout_quand_la_liste_blanche_est_vide() -> None:
    with pytest.raises(SQLValidationError):
        validate("select * from dim_patient", set())


# --------------------------------------------------------------------------- #
# Regle 5 : fonctions d'acces fichiers, reseau, environnement, catalogue
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "select * from read_csv('/etc/passwd')",
        "select * from read_csv_auto('data/raw/hosp/patients.csv.gz')",
        "select * from read_parquet('s3://seau/fichier.parquet')",
        "select * from read_json('https://exemple.org/fuite.json')",
        "select * from glob('/home/*')",
        "select * from query('drop table dim_patient')",
        "select * from duckdb_tables()",
        "select * from duckdb_settings()",
        "select * from range(10)",
        "select * from sniff_csv('x.csv')",
    ],
)
def test_rejette_une_fonction_en_position_de_table(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate(sql, TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from range(10)",
        "select * from generate_series(1, 3)",
        # Fonction (macro) portant le nom d'une table de la liste blanche.
        "select * from dim_patient()",
    ],
)
def test_rejette_une_fonction_de_table_absente_de_la_liste_noire(sql: str) -> None:
    _rejette(sql, "fonction en position de table")


@pytest.mark.parametrize(
    ("sql", "fonction"),
    [
        ("select read_text('/etc/passwd') from dim_patient", "read_text"),
        ("select read_blob('/etc/shadow') from dim_patient", "read_blob"),
        ("select getenv('MISTRAL_API_KEY') from dim_patient", "getenv"),
        ("select current_setting('home_directory') from dim_patient", "current_setting"),
        (
            "select * from dim_patient where subject_id in (select * from read_csv('/etc/passwd'))",
            "read_csv",
        ),
        ("select count(*) from dim_patient where exists (select glob('*'))", "glob"),
        ("select pragma_version() from dim_patient", "pragma_version"),
    ],
)
def test_rejette_une_fonction_interdite_hors_du_from(sql: str, fonction: str) -> None:
    _rejette(sql, f"fonction interdite : {fonction}")


def test_le_message_d_erreur_ne_recopie_pas_le_sql() -> None:
    with pytest.raises(SQLValidationError) as erreur:
        validate("select * from dim_patient; drop table dim_patient", TABLES)
    assert "drop table" not in str(erreur.value).lower()
