# anamnese — Plan d'exécution

Huit étapes. Chacune a un critère de sortie vérifiable. **On ne passe pas à la
suivante tant que le critère n'est pas atteint.**

Les blocs `>` sont les messages à donner à Claude Code, un par étape.

---

## Étape 0 — Socle

> Initialise le projet `anamnese` selon l'arborescence de CLAUDE.md §4.
> Crée : `pyproject.toml` (uv, Python 3.12, dépendances duckdb, dbt-core~=1.12,
> dbt-duckdb, sqlglot, mistralai, pydantic-settings, fastapi, uvicorn,
> streamlit, pytest, ruff), `.gitignore` (data/, .env, target/, logs/,
> __pycache__), `.env.example`, `Makefile` (cibles : data, build, eval, serve,
> test, lint), `README.md` minimal, et les dossiers vides avec un `.gitkeep`.
> Ne crée aucun code métier à ce stade.

**Sortie :** `uv sync` passe, `make lint` passe, `tree` correspond à §4.

---

## Étape 1 — Données

> Écris `scripts/download_mimic.py` : télécharge MIMIC-IV demo 2.2 depuis
> `https://physionet.org/files/mimic-iv-demo/2.2/` vers `data/raw/`, en
> conservant l'arborescence `hosp/` et `icu/`. Reprise sur interruption, pas de
> re-téléchargement si le fichier existe avec la bonne taille, barre de
> progression.
>
> Puis inspecte réellement les fichiers : pour chaque CSV.gz, affiche le nom des
> colonnes, les types inférés par DuckDB et le nombre de lignes. Écris le
> résultat dans `data/raw/_inventory.md`. Ne devine rien.

**Sortie :** `data/raw/hosp/` et `data/raw/icu/` peuplés, `_inventory.md` lisible.
C'est ce fichier qui servira de référence pour tous les modèles dbt.

---

## Étape 2 — dbt staging

> Initialise le projet dbt dans `transform/` avec l'adapter duckdb pointant vers
> `data/warehouse/anamnese.duckdb`.
>
> Déclare dans `models/staging/_sources.yml` les tables suivantes, en te basant
> **exclusivement** sur `data/raw/_inventory.md` :
> hosp : patients, admissions, diagnoses_icd, d_icd_diagnoses, procedures_icd,
> d_icd_procedures, labevents, d_labitems.
> icu : icustays, chartevents, d_items.
>
> Crée une vue de staging par table : renommage en snake_case cohérent, cast
> explicite des dates et numériques, aucune jointure, aucune logique métier.
> Types déclarés explicitement — pas d'inférence sur `labevents.value`.

**Sortie :** `dbt build --select staging` vert. `dbt docs generate` produit un
`manifest.json`.

---

## Étape 3 — Marts documentés

> Crée les modèles intermediate et marts :
> - `int_diagnoses_labelled` : diagnoses_icd × d_icd_diagnoses, en gérant les
>   deux versions CIM (9 et 10).
> - `int_lab_results_labelled` : labevents × d_labitems.
> - `dim_patient` : une ligne par patient.
> - `fct_admission` : une ligne par séjour hospitalier, avec durée de séjour,
>   type d'admission, mode de sortie, et le diagnostic principal dénormalisé.
> - `fct_icu_stay` : une ligne par séjour en réanimation.
> - `fct_lab_result` : une ligne par résultat d'analyse, avec libellé et unité.
>
> Puis rédige `models/marts/_marts.yml` en appliquant strictement CLAUDE.md §5 :
> granularité au niveau du modèle, et pour chaque colonne l'unité, les valeurs
> énumérées, le référentiel de code, et la signification d'un NULL. Vérifie les
> valeurs réellement présentes en base pour les énumérations — ne les invente pas.
>
> Ajoute les tests unique / not_null / relationships.

**Sortie :** `dbt build` vert, tests inclus. Relis `_marts.yml` ligne par ligne :
une description que tu ne pourrais pas utiliser pour écrire une requête sans
ouvrir la table est à réécrire. **C'est l'étape qui détermine la qualité finale
du projet — ne pas la bâcler pour avancer.**

---

## Étape 4 — Garde-fous, avant tout LLM

> Écris `src/anamnese/config.py` (pydantic-settings) puis
> `src/anamnese/guardrails.py` en suivant la spécification de CLAUDE.md §3.
>
> Écris ensuite `tests/test_guardrails.py` avec au minimum un test par règle et
> les cas d'injection listés dans la spec. Les tests d'abord si tu préfères, mais
> les deux dans la même étape.

**Sortie :** `pytest tests/test_guardrails.py` vert, couverture complète des
règles §3. Aucun appel LLM n'a encore été écrit.

---

## Étape 5 — Contexte et moteur

> Écris :
> - `catalog.py` : lit `transform/target/manifest.json`, extrait les modèles du
>   dossier marts, produit le contexte texte (table, colonnes, types,
>   descriptions) et la whitelist de tables.
> - `prompts/system.md` : prompt système, en anglais, exigeant une seule requête
>   SELECT DuckDB sans explication, avec le contexte injecté par placeholder.
> - `prompts/examples.yml` : 5 exemples question → SQL couvrant une agrégation,
>   une jointure de libellé, un filtre temporel, un calcul de durée, un COUNT
>   DISTINCT.
> - `llm.py` : client Mistral, modèle lu depuis `MISTRAL_MODEL`, température 0,
>   extraction du SQL hors des fences markdown.
> - `engine.py` : orchestration contexte → LLM → validate → exécution read_only,
>   avec 2 retries maximum en réinjectant le message d'erreur.
>
> Ajoute une commande CLI `python -m anamnese.engine "<question>"` pour tester.

**Sortie :** trois questions posées en CLI renvoient un SQL valide et un résultat
plausible, vérifié à la main.

---

## Étape 6 — Évaluation

> Écris `eval/questions.yml` avec 30 questions couvrant : comptages simples,
> agrégations, jointures code → libellé, filtres temporels, calculs de durée,
> sous-requêtes, questions ambiguës volontairement mal posées. Pour chacune,
> rédige le SQL attendu **toi-même**, en le vérifiant sur la base.
>
> Puis `eval/run_eval.py` selon CLAUDE.md §9 : comparaison des DataFrames de
> résultats, insensible à l'ordre sauf question de tri, rapport avec taux global,
> taux par tag, échecs détaillés, modèle et coût.

**Sortie :** `make eval` produit un rapport. Note le taux de référence — c'est
ton point de comparaison pour tout changement ultérieur.

Lance ensuite l'éval sur les modèles accessibles au forfait (`mistral-small` et
`mistral-large` ne l'étaient pas : `codestral`, `ministral-3b`, `-8b`, `-14b`).
Le tableau des taux décide du modèle, pas l'intuition.

---

## Étape 7 — API et interface

> Écris `src/anamnese/api.py` (FastAPI) : endpoint `POST /ask` prenant une
> question, renvoyant `{sql, colonnes, lignes, duree_ms}` ; endpoint `/health`
> vérifiant la présence du warehouse. Gestion propre du warehouse absent.
>
> Puis `app/streamlit_app.py` : champ question, affichage du SQL généré dans un
> bloc dépliable, tableau de résultats, bouton de téléchargement CSV, historique
> de session. L'UI appelle l'API en HTTP, elle n'ouvre jamais DuckDB.

**Sortie :** `make serve` lance les deux, une question posée dans le navigateur
renvoie un résultat.

---

## Étape 8 — Docker

> Écris `docker-compose.yml` et les trois Dockerfiles selon CLAUDE.md §8.
> Vérifie qu'un `docker compose run dbt` reconstruit le warehouse et que l'API
> voit le nouveau fichier sans redémarrage, ou documente la limite si ce n'est
> pas le cas.

**Sortie :** `docker compose up` depuis un clone propre, après `make data`,
donne une application fonctionnelle.

---

## Pièges connus

- **`labevents.value` est textuelle.** Elle contient des valeurs comme
  `"<0.1"` ou `"GREATER THAN 300"`. Utiliser `valuenum` pour tout calcul, et le
  documenter dans `_marts.yml`.
- **Deux versions de CIM coexistent** dans `diagnoses_icd` (colonne
  `icd_version`). Une jointure sur le seul `icd_code` produit des doublons.
- **Les dates sont décalées dans le futur** par la désidentification. Les durées
  et les écarts restent justes, les dates absolues n'ont aucun sens. À écrire
  noir sur blanc dans les descriptions, sinon le LLM produira des filtres
  temporels absurdes.
- **`chartevents` est volumineuse** même dans le demo. Ne pas l'exposer telle
  quelle en mart.
- **DuckDB n'accepte qu'un écrivain.** Si l'API tourne, `dbt build` échoue.
  D'où le build dans un fichier temporaire suivi d'un rename.
