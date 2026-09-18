# anamnese

Poser une question en francais, obtenir une reponse tiree d'une base clinique.

dbt construit et documente des marts dans DuckDB a partir de MIMIC-IV demo 2.2,
un modele Mistral traduit la question en SQL a partir de ce schema documente,
et DuckDB execute la requete en lecture seule derriere des garde-fous.

Le modele ne voit jamais une ligne de donnees : uniquement des metadonnees.

## Architecture

```
MIMIC-IV demo (CSV.gz)
        |
        v
    dbt  staging -> intermediate -> marts        manifest.json
        |                                              |
        v                                              v
   DuckDB (lecture seule)  <---- SQL valide ----  moteur NL2SQL
        |                                              ^
        |                                              |
        +-----> resultats -----> API -----> Streamlit --+
```

| Couche | Role |
|---|---|
| `transform/` | dbt : construit et **documente** les marts. Les `description:` pilotent la qualite du NL2SQL. |
| `src/anamnese/catalog.py` | Lit `manifest.json`, fabrique le contexte envoye au modele. |
| `src/anamnese/guardrails.py` | Valide le SQL genere. SELECT uniquement, tables en liste blanche, aucun acces fichier ni reseau. |
| `src/anamnese/engine.py` | Orchestration : contexte, appel modele, validation, execution, retry. |
| `eval/` | Jeu de questions de reference. C'est lui qui tranche le choix du modele. |

## Demarrage

```bash
make setup     # dependances, .env, hooks git
# renseigner MISTRAL_API_KEY dans .env
make data      # telecharge MIMIC-IV demo 2.2 et inventorie les colonnes
make build     # reconstruit le warehouse DuckDB
make serve     # API sur :8000, interface sur :8501
```

`make help` liste toutes les cibles.

## Developpement

```bash
make check     # lint + tests : la porte de sortie locale
make sql-lint  # nomenclature SQL des modeles dbt
make eval      # rejoue le jeu d'evaluation et ecrit un rapport
```

Les hooks git (`make hooks`) refusent un commit qui contient un secret, un
fichier de `data/`, un nom de fichier fourre-tout, ou un message hors du format
Conventional Commits. Le pre-push rejoue les tests.

## Regles du projet

[`CLAUDE.md`](CLAUDE.md) est le contrat : stack, garde-fous, nomenclature, git.
[`PLAN.md`](PLAN.md) est la feuille de route, en huit etapes a critere de sortie
verifiable.

## Donnees

MIMIC-IV Clinical Database Demo 2.2 — 100 patients, librement accessible.
<https://physionet.org/content/mimic-iv-demo/2.2/>

La version complete de MIMIC-IV exige un acces accredite (formation CITI et
licence PhysioNet). Les deux ne se melangent pas dans ce depot.

Les dates de MIMIC sont decalees par la desidentification : les durees et les
ecarts restent justes, les dates absolues n'ont aucun sens.

## Licence

MIT pour le code. Les donnees MIMIC-IV relevent de leur propre licence
PhysioNet et ne sont pas redistribuees ici.
