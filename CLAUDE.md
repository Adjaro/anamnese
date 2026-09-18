# anamnese — Règles du projet

NL2SQL sur MIMIC-IV demo : dbt construit et documente des marts dans DuckDB,
un LLM Mistral traduit une question en SQL à partir du schéma documenté,
DuckDB exécute en lecture seule derrière des garde-fous.

---

## 1. Stack imposée — ne rien substituer

| Composant     | Choix                      | Interdit                                      |
|---------------|----------------------------|-----------------------------------------------|
| Transformation| dbt-core 1.12.x + dbt-duckdb| dbt v2 / dbt-oss (adapter DuckDB en Beta)     |
| Base          | DuckDB (fichier)           | Postgres, SQLite, tout serveur                |
| LLM           | API Mistral                | OpenAI, Anthropic, tout autre fournisseur     |
| Validation SQL| sqlglot (dialecte duckdb)  | regex, `str.startswith("SELECT")`             |
| API           | FastAPI + uvicorn          | Flask, Django                                 |
| UI            | Streamlit (client de l'API)| logique métier dans l'UI                      |
| Packaging     | uv + pyproject.toml        | requirements.txt, poetry, conda               |
| Exécution     | Docker Compose             | installation hôte                             |

Le modèle Mistral n'est **jamais** codé en dur : il vient de `MISTRAL_MODEL`
(défaut `codestral-latest`, choisi par `make eval` : 97 % contre 90 % pour
`ministral-14b-latest`, voir PR de l'étape 6). Le code doit fonctionner à
l'identique avec n'importe quel modèle Mistral de chat.

Avant d'ajouter une dépendance non listée dans `pyproject.toml` : demander.

---

## 2. Règles absolues

1. **Ne jamais inventer une colonne ou une table.** Toute référence à MIMIC-IV
   doit être vérifiée dans `data/raw/` ou via `DESCRIBE` sur DuckDB avant
   d'écrire un modèle. En cas de doute : lire le CSV, ne pas supposer.
2. **`data/` n'est jamais commité.** Ni les CSV, ni le `.duckdb`, ni les exports.
   Le warehouse est un artefact reconstructible par `make build`.
3. **Aucun secret dans le code ni dans git.** `MISTRAL_API_KEY` vient de `.env`,
   qui est gitignored. Seul `.env.example` est versionné, avec des valeurs vides.
4. **Le LLM ne voit jamais de données.** Il reçoit uniquement des métadonnées :
   noms de tables, de colonnes, types, descriptions. Jamais une ligne, jamais un
   échantillon de valeurs, jamais un résultat de requête.
5. **Le SQL généré est toujours validé avant exécution**, et toujours exécuté sur
   une connexion `read_only=True`. Aucune exception, même en développement.
6. **Ne pas créer de fichier hors de l'arborescence définie en §4.** Pas de
   script à la racine, pas de notebook « temporaire », pas de dossier `utils/`.
7. **Ne pas passer à l'étape suivante si la précédente n'est pas verte.**
   Le critère de sortie de chaque étape est défini dans `PLAN.md`.

---

## 3. Garde-fous SQL — spécification

`src/anamnese/guardrails.py` est le seul module où un bug est une faille.
Il est écrit et testé **avant** que le moteur LLM fonctionne.

La fonction `validate(sql, allowed_tables)` doit :

- parser avec `sqlglot.parse_one(sql, read="duckdb")` ; toute erreur de parsing
  est un rejet ;
- rejeter si la racine n'est pas `exp.Select` ou `exp.Union` ;
- rejeter la présence de `exp.Insert`, `exp.Update`, `exp.Delete`, `exp.Drop`,
  `exp.Create`, `exp.Alter`, `exp.Command` n'importe où dans l'arbre ;
- rejeter toute table non présente dans `allowed_tables`, en tolérant les CTE
  déclarés dans la requête elle-même ;
- rejeter les fonctions d'accès au système de fichiers et au réseau :
  `read_csv`, `read_parquet`, `read_json`, `glob`, `httpfs`, `ATTACH`, `COPY`,
  `INSTALL`, `LOAD` ;
- retourner le SQL normalisé par sqlglot, jamais la chaîne brute du LLM.

L'exécution ajoute une enveloppe `SELECT * FROM (<sql>) LIMIT :max_rows` et un
timeout. La connexion est ouverte avec
`duckdb.connect(path, read_only=True, config={"enable_external_access": False})`.

Chaque règle ci-dessus a un test dédié dans `tests/test_guardrails.py`, avec au
moins un cas d'injection par règle (`; DROP TABLE`, commentaire `--`, UNION vers
une table hors whitelist, CTE masquant un nom autorisé).

---

## 4. Arborescence — autorité

```
anamnese/
├── CLAUDE.md  PLAN.md  README.md  Makefile
├── pyproject.toml  .env.example  .gitignore
├── .pre-commit-config.yaml  .sqlfluff  .yamllint.yml  .gitmessage
├── .secrets.baseline        # reference detect-secrets, regenerer si faux positif
├── .github/
│   ├── CODEOWNERS  pull_request_template.md
│   └── workflows/{ci.yml,pr-title.yml}
├── docker-compose.yml  docker/{api.Dockerfile,dbt.Dockerfile,app.Dockerfile}
├── data/                    # gitignored en entier, créé par les scripts
│   ├── raw/                 # CSV.gz PhysioNet, en lecture seule
│   └── warehouse/           # anamnese.duckdb, .build/ pendant un build
├── scripts/download_mimic.py  scripts/swap_warehouse.py
├── transform/               # projet dbt isolé
│   ├── dbt_project.yml  profiles.yml  packages.yml
│   └── models/{staging/{hosp,icu},intermediate,marts}/
├── src/anamnese/
│   ├── config.py  catalog.py  llm.py  guardrails.py  engine.py  api.py
│   └── prompts/{system.md,examples.yml}
├── eval/{questions.yml,run_eval.py}
├── app/streamlit_app.py
├── docs/captures/           # captures d'ecran du README
└── tests/{test_conventions.py,test_guardrails.py,test_catalog.py,test_engine.py,test_api.py}
```

`data/` est gitignoré en entier — pas de `.gitkeep`, les scripts créent les
dossiers avec `mkdir(parents=True, exist_ok=True)`.

Créer un fichier ailleurs = demander d'abord.

---

## 5. Conventions dbt

- Nommage : `stg_<source>__<table>`, `int_<description>`, `dim_<entité>`,
  `fct_<événement>`. Double underscore uniquement après le préfixe de source.
- `staging` : matérialisé en `view`, une vue par table source, aucune jointure.
  Rôle unique : renommer, typer, caster les dates. Pas de logique métier.
- `intermediate` : matérialisé en `ephemeral`. Jointures code → libellé
  (`diagnoses_icd` × `d_icd_diagnoses`, `labevents` × `d_labitems`).
- `marts` : matérialisé en `table`. Dénormalisé et large. C'est le seul niveau
  exposé au LLM.
- Un fichier `.yml` par couche, à côté des modèles. Jamais de `schema.yml`
  monolithique à la racine.
- Tests obligatoires sur chaque mart : `unique` et `not_null` sur la clé,
  `relationships` sur chaque clé étrangère.
- `ref()` partout. Jamais de nom de table en dur dans un modèle.

### Descriptions — le point critique

Les `description:` des marts pilotent la qualité du NL2SQL davantage que le
prompt. Elles sont écrites pour une machine, pas pour un rapport.

Chaque **modèle** précise sa granularité : « une ligne par séjour hospitalier ».

Chaque **colonne** précise, selon le cas : l'unité (`durée en heures`), les
valeurs possibles énumérées (`URGENT, ELECTIVE, EW EMER.`), le référentiel du
code (`CIM-9 ou CIM-10, voir icd_version`), la signification d'un NULL
(`NULL si le patient est toujours hospitalisé`).

Une description vide ou décorative (`identifiant du patient` sur `subject_id`)
est considérée comme un défaut à corriger.

---

## 6. Conventions Python

- Python 3.12. Type hints sur toute fonction publique. `from __future__ import
  annotations` en tête.
- Configuration via `pydantic-settings` dans `config.py`. Aucun `os.getenv`
  ailleurs dans le code.
- Le prompt système vit dans `src/anamnese/prompts/system.md`, chargé depuis le
  disque. Jamais de prompt en f-string dans le code : une modification de prompt
  doit apparaître dans un diff git.
- Les exemples few-shot vivent dans `prompts/examples.yml`.
- Erreurs : exceptions maison, définies dans le module qui les lève :
  `SQLValidationError` dans `guardrails.py`, `LLMError` dans `llm.py`. `engine.py`
  les importe ; les définir dans `engine.py` créerait un import circulaire, puisque
  `engine.py` importe ces modules. Pas de `except Exception: pass`.
- Logs : `structlog` ou `logging` en JSON. On journalise systématiquement la
  question, le SQL généré, le verdict de validation et la durée. Jamais les
  lignes de résultat.
- Lint et format : `ruff check` et `ruff format`. Le code doit passer avant
  chaque fin d'étape.

---

## 7. Contraintes DuckDB

- **Un seul écrivain.** dbt écrit, l'API lit. Les deux ne doivent jamais ouvrir
  le fichier en écriture simultanément.
- Le build écrit dans `data/warehouse/.build/anamnese.duckdb`, puis
  `os.replace()` vers `anamnese.duckdb` — un rename atomique. C'est le rôle de
  `scripts/swap_warehouse.py`. Le fichier temporaire garde le nom
  `anamnese.duckdb` : dbt-duckdb exige que le nom du catalogue corresponde au
  nom de fichier, un suffixe `.tmp` casse le build.
- L'API ouvre le fichier en `read_only`. Si le fichier est absent, elle démarre
  quand même et renvoie une erreur explicite sur `/ask`, sans planter.
- Les CSV sont lus via `read_csv_auto` avec les types déclarés explicitement
  dans les sources dbt. Ne pas se fier à l'inférence sur `labevents`, dont la
  colonne `value` est textuelle malgré les apparences.

---

## 8. Docker

Trois services dans `docker-compose.yml`, un volume partagé `./data` :

- `dbt` : one-shot, exécute le build puis se termine. `profiles.yml` pointe vers
  `/data/warehouse/`.
- `api` : FastAPI sur le port 8000, monte `./data` en lecture seule.
- `app` : Streamlit sur 8501, ne parle qu'à `api`, jamais à DuckDB.

`MISTRAL_API_KEY` passe par `env_file: .env`. Jamais en `ARG`, jamais dans une
couche d'image.

---

## 9. Évaluation

`eval/questions.yml` contient des entrées de la forme :

```yaml
- id: q01
  question: "Combien de patients ont eu plus d'un séjour en réanimation ?"
  sql_attendu: "SELECT ..."
  tags: [agrégation, icu]
```

`run_eval.py` exécute le SQL attendu et le SQL généré, puis **compare les
DataFrames de résultats**, pas le texte des requêtes : plusieurs formulations
SQL sont correctes. La comparaison est insensible à l'ordre des lignes et des
colonnes sauf si la question porte sur un tri.

Sortie : taux de réussite global, échecs détaillés, taux par tag, modèle et coût.

L'éval est rejouée à chaque modification de prompt, de modèle ou de mart. Une
régression bloque l'étape.

---

## 10. Nomenclature

### Fichiers et dossiers

- Dossiers et modules Python : `snake_case`. Un module qui expose un concept est
  au singulier (`catalog.py`, `engine.py`) ; un dossier qui contient une
  collection de ressources est au pluriel (`prompts/`, `models/`, `tests/`).
- Fichiers Python : `snake_case.py`. Un fichier = une responsabilité. Si un
  nom nécessite un « et » pour être décrit, le fichier est à scinder.
- Fichiers de configuration YAML dbt : préfixe `_` pour les fichiers de
  métadonnées (`_sources.yml`, `_marts.yml`), afin qu'ils remontent en tête du
  tri alphabétique à côté des modèles qu'ils décrivent.
- Scripts exécutables : verbe à l'infinitif en tête (`download_mimic.py`,
  `swap_warehouse.py`, `run_eval.py`). Un script se nomme par ce qu'il fait.
- Aucun fichier nommé `utils`, `helpers`, `common`, `misc`, `tools`, `temp`,
  `test2`, `final`, `new`. Ce sont des aveux d'absence de modèle mental.
- Aucun suffixe de version dans un nom de fichier (`engine_v2.py`). C'est le
  rôle de git.

### SQL et dbt

- Mots-clés SQL en minuscules. Identifiants en `snake_case`. Une clause par
  ligne, virgule en tête de ligne dans les longues listes de colonnes.
- CTE nommées explicitement (`admissions_filtrees`), jamais `a`, `t1`, `tmp`.
  Structure imposée : CTE d'import en tête (une par `ref()`), puis CTE de
  logique, puis un `select final` unique en fin de fichier.
- Alias de table : le nom complet ou une abréviation lisible. Jamais une lettre.
- Clés primaires : `<entité>_id` au singulier (`patient_id`, `admission_id`).
  Les clés techniques de MIMIC conservent leur nom d'origine (`subject_id`,
  `hadm_id`, `stay_id`) — on ne renomme pas un identifiant que l'utilisateur
  métier connaît sous ce nom.
- Booléens : préfixe `est_` ou `a_` (`est_deces_hospitalier`, `a_sejour_icu`).
  Jamais `flag_`, jamais `is_x_yn`.
- Dates et horodatages : suffixe `_date` pour une date, `_at` pour un
  horodatage (`admission_at`, `sortie_at`, `naissance_date`).
- Durées : suffixe explicitant l'unité (`duree_sejour_heures`,
  `delai_admission_icu_jours`). Une durée sans unité dans son nom est un bug.
- Montants et mesures : suffixe d'unité systématique (`valeur_num`, `unite`).
- Agrégats : préfixe de la fonction (`nb_sejours`, `total_examens`,
  `moyenne_duree_heures`, `min_`, `max_`). Jamais `count`, `sum` seuls.
- Colonnes issues d'une source non modifiée : nom d'origine conservé en
  staging, renommé une seule fois, en intermediate ou en mart. Pas deux
  renommages successifs.

### Python

- Classes en `PascalCase`, fonctions et variables en `snake_case`, constantes
  de module en `SCREAMING_SNAKE_CASE`.
- Préfixe `_` pour tout ce qui n'est pas destiné à être importé hors du module.
- Fonctions : verbe en tête (`build_context`, `validate_sql`, `run_query`).
  Les prédicats commencent par `is_` ou `has_` et retournent un booléen strict.
- Pas d'abréviation sauf celles du domaine (`sql`, `llm`, `icd`, `icu`, `df`).
  `configuration` s'écrit `config`, `question` ne s'écrit pas `q`.
- Exceptions maison : suffixe `Error` (`SQLValidationError`), jamais
  `Exception` ni `Failure`.
- Fichiers de test : `test_<module>.py`, fonctions `test_<comportement_attendu>`
  rédigées comme une phrase (`test_rejette_union_vers_table_non_autorisee`).
  Un nom de test doit se lire dans le rapport d'échec sans ouvrir le fichier.

### Variables d'environnement

- `SCREAMING_SNAKE_CASE`, préfixées par domaine : `MISTRAL_API_KEY`,
  `MISTRAL_MODEL`, `ANAMNESE_DUCKDB_PATH`, `ANAMNESE_MAX_ROWS`,
  `ANAMNESE_LOG_LEVEL`.
- Toute variable présente dans `.env.example` avec un commentaire d'une ligne
  et une valeur par défaut quand elle en admet une. Une variable non documentée
  dans `.env.example` n'existe pas.

### API

- Routes en `kebab-case`, au pluriel pour les collections. Verbe HTTP porteur de
  l'action : `POST /ask`, `GET /health`, `GET /catalog/tables`.
- Champs JSON en `snake_case`, cohérents avec les noms de colonnes.
- Codes de retour : `400` pour une question invalide, `422` pour un SQL rejeté
  par les garde-fous, `503` pour un warehouse absent, `502` pour un échec LLM.

### Docker

- Services en minuscules, un mot : `dbt`, `api`, `app`.
- Images taguées `anamnese/<service>:<version>`. Jamais `latest` en dehors du
  développement local.

---

## 11. Git

### Branches

- `main` est toujours déployable. Aucun commit direct dessus : le hook
  `no-commit-to-branch` le refuse, et la branche est protégée côté distant.
- Nommage : `<type>/<description-courte>` en kebab-case —
  `feat/marts-fct-admission`, `fix/guardrails-cte-shadowing`,
  `docs/descriptions-labevents`. Jamais de branche au nom d'une personne ni
  d'un numéro de ticket seul.
- Une branche = une étape de `PLAN.md`, ou un correctif. Une branche qui vit
  plus de deux jours est à découper.
- Rebase sur `main` avant d'ouvrir la PR. Jamais de merge de `main` dans la
  branche : l'historique doit rester linéaire et lisible.

### Commits

- Format Conventional Commits :
  `<type>(<scope>): <description à l'impératif, minuscule, sans point final>`,
  72 caractères maximum sur la ligne de sujet.
- Types : `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `build`,
  `ci`, `revert`.
- Scopes : `dbt`, `guardrails`, `engine`, `api`, `app`, `eval`, `docker`,
  `data`. Le scope est omis pour un changement transverse qui ne relève
  d'aucun d'eux (outillage, CI) : `ci: ajoute le job hygiene`.
  Exemple : `feat(dbt): ajoute fct_icu_stay et ses tests de relation`.
- Un commit = un changement cohérent. Un commit qui touche à la fois un modèle
  dbt, le prompt et le Dockerfile est à scinder.
- Le corps explique **pourquoi**, jamais **quoi** : le diff dit déjà quoi. Une
  décision d'architecture ou un contournement non évident mérite trois lignes.
- `BREAKING CHANGE:` en pied de page dès qu'un contrat change : schéma d'un
  mart, forme d'une réponse d'API, nom d'une variable d'environnement.
- Le template `.gitmessage` est installé par `make hooks`. Ne pas le contourner
  avec `git commit -m` pour un changement non trivial.

### Ce qui n'est jamais commité

`data/`, `.env`, `transform/target/`, `transform/logs/`, `eval/reports/`, tout
fichier `.duckdb`, `.csv`, `.parquet`, toute clé API, tout notebook exécuté avec
ses sorties. Trois filets successifs : `.gitignore`, les hooks locaux, le job
`hygiene` de la CI.

### Portes de qualité

| Moment | Ce qui tourne | Durée visée |
|---|---|---|
| `pre-commit` | ruff, detect-secrets, sqlfluff, yamllint, fichiers interdits | < 5 s |
| `commit-msg` | format Conventional Commits | instantané |
| `pre-push` | `pytest`, `dbt parse` | < 60 s |
| CI sur PR | gitleaks, lint, tests, hygiène du dépôt | < 5 min |

Deux scanners de secrets, volontairement : `detect-secrets` en local, parce qu'il s'installe sans chaîne Go et fonctionne sur un poste à réseau restreint ; `gitleaks` en CI, où GitHub le fournit déjà compilé. Un faux positif se marque `pragma: allowlist secret` ou se régénère avec
`uv run detect-secrets scan --baseline .secrets.baseline`.

Un hook qui échoue se corrige, il ne se contourne pas. `--no-verify` est
réservé à un dépannage, et le commit suivant doit remettre le dépôt d'aplomb.

### Pull requests

- Le titre suit le même format que les commits — vérifié par `pr-title.yml`.
- Le template est rempli en entier. Une case cochée à tort est un mensonge dans
  l'historique du projet.
- Toute PR touchant le prompt, le modèle ou un mart **doit** porter le tableau
  avant / après issu de `make eval`. Une régression du taux bloque la fusion.
- `guardrails.py` et ses tests exigent une revue explicite : c'est le seul
  module où un bug est une faille.
- Fusion en **squash**, le message de squash reprenant le titre de la PR.
  Historique linéaire, un commit par changement fonctionnel.
- Supprimer la branche après fusion.

### Protection de `main` — à configurer côté distant

Exiger : une revue approuvée, les checks `secrets`, `quality`, `hygiene` et
`conventional` au vert, la branche à jour avant fusion, aucun push forcé,
aucune suppression, et les règles appliquées aussi aux administrateurs.

### Historique

- Réécrire l'historique d'une branche non partagée : autorisé et encouragé
  (`rebase -i` pour recoller les commits de mise au point).
- Réécrire l'historique d'une branche déjà poussée et relue : interdit.
- Annuler un changement déjà sur `main` : `git revert`, jamais `reset --hard`
  suivi d'un push forcé.
- Une clé accidentellement poussée est **compromise**, même après réécriture de
  l'historique. La procédure est : révoquer la clé chez Mistral, en générer une
  nouvelle, puis nettoyer l'historique — dans cet ordre.

---

## 12. Commandes

`make help` fait foi. Les cibles qui comptent :

| Commande | Effet |
|---|---|
| `make setup` | Dépendances, `.env`, hooks git. À lancer une fois. |
| `make data` | Télécharge MIMIC-IV demo 2.2 et écrit `data/raw/_inventory.md`. |
| `make build` | Reconstruit le warehouse (fichier temporaire puis swap atomique). |
| `make docs` | Régénère `manifest.json`, le contexte envoyé au LLM. |
| `make check` | `lint` + `test`. La porte de sortie locale, identique à la CI. |
| `make sql-lint` | Nomenclature SQL des modèles dbt. |
| `make eval` | Rejoue le jeu d'évaluation et écrit un rapport. |
| `make serve` | API et interface via Docker Compose. |

Les dépendances sont gérées par `uv`, en trois ensembles : le runtime dans
`[project.dependencies]`, dbt dans le groupe `dbt`, l'outillage dans le groupe
`dev`. dbt est isolé volontairement : le service `api` ne doit pas l'embarquer,
et ses contraintes de version ne doivent pas remonter dans le runtime.

---

## 13. Interaction attendue

- Répondre en français, de façon directe et technique. Pas de préambule.
- Livrer du **code complet** plutôt que des diffs partiels.
- Avant d'écrire un modèle dbt : inspecter la source réelle. Ne jamais déduire un
  schéma de son nom.
- Après chaque modification de `transform/` : `dbt build` et rapporter le
  résultat. Un modèle non testé n'est pas terminé.
- Une étape de `PLAN.md` n'est terminée que quand `make check` passe et que son
  critère de sortie est atteint. Le dire explicitement avant de passer à la
  suivante.
- Signaler tout écart avec ces règles plutôt que de le contourner
  silencieusement. Si une règle bloque, le dire et proposer, ne pas la
  réinterpréter.
- Commiter est autorisé, dans le cadre de §11 : sur une branche
  `<type>/<description>`, jamais sur `main`, hooks actifs, jamais de
  `--no-verify`. Un commit par changement cohérent, message passé par fichier
  (`git commit -F`) avec un corps qui explique pourquoi.
- Ne jamais pousser, ouvrir de PR, fusionner, ni réécrire un historique déjà
  poussé sans demande explicite de l'humain pour cette action précise.
