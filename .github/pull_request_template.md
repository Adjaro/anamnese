## Pourquoi

<!-- Le probleme resolu ou l'etape de PLAN.md couverte. Le diff dit deja quoi. -->

Etape de `PLAN.md` : <!-- ex. Etape 3 — Marts documentes, ou « correctif » -->

## Critere de sortie

<!-- Recopier le critere de l'etape et montrer qu'il est atteint. -->

## Evaluation

<!-- Obligatoire si la PR touche le prompt, le modele ou un mart (CLAUDE.md §11).
Tableau issu de `make eval`. Une regression du taux bloque la fusion. -->

| Mesure | Avant | Apres |
|---|---|---|
| Taux global | | |
| Modele | | |

## Verifications

<!-- Une case cochee a tort est un mensonge dans l'historique du projet. -->

- [ ] `make check` passe en local
- [ ] `dbt build` vert si `transform/` est modifie, resultat colle ci-dessus
- [ ] Tableau avant / apres `make eval` si prompt, modele ou mart modifie
- [ ] Aucune colonne ni table inventee : chaque reference verifiee sur la source
- [ ] Aucun fichier de `data/`, aucun secret, aucun artefact de build
- [ ] `BREAKING CHANGE:` en pied de commit si un contrat change
- [ ] Revue explicite demandee si `guardrails.py` ou ses tests sont modifies
