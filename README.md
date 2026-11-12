# Veille hebdomadaire déchets textiles — Europe / France / Normandie

Workflow Mistral qui génère chaque lundi un rapport d'analyse sur les sujets
liés aux déchets textiles à trois périmètres : Europe, France, Normandie.

## Fonctionnement

1. **Collecte** — trois activités en parallèle recherchent sur le web les
   actualités des 7 derniers jours (`research_scope`) : réglementation, filière
   (collecte, tri, réemploi, recyclage), innovation, financements, événements,
   chiffres clés. Chaque périmètre a ses sources privilégiées (institutionnelles,
   professionnelles et locales).
2. **Synthèse** — une activité LLM rédige le rapport en français
   (`synthesize_report`) : « L'essentiel de la semaine », sections Europe /
   France / Normandie avec liens sources, « À suivre », « Sources ».
3. **Publication** — le rapport Markdown est déposé dans une bibliothèque de
   documents Mistral (`publish_report`) si `library_id` est fourni (input ou
   variable d'environnement `REPORT_LIBRARY_ID`).

## Workflow

- Nom : `textile-waste-weekly-report`
- Entrée : `{"weeks_back": 1, "library_id": null, "dry_run": false}`
- Sortie : période couverte, nombre d'actualités, rapport Markdown, identifiant
  du document publié.

## Développement local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # renseigner MISTRAL_API_KEY
pytest
python -m worker.entrypoints.worker  # démarre le worker
```

## Déploiement

Le déploiement managé se fait depuis un dépôt GitHub avec le Dockerfile
`deploy/docker/Dockerfile.worker`. Après déploiement, créer la planification
hebdomadaire (chaque lundi, heure de Paris) via l'API des planifications de
workflows, par exemple :

```bash
curl -X POST "https://api.mistral.ai/v1/workflows/schedules" \
  -H "Authorization: Bearer $MISTRAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_identifier": "textile-waste-weekly-report",
    "schedule": {
      "input": {},
      "cron_expressions": ["0 7 * * 1"],
      "time_zone_name": "Europe/Paris"
    }
  }'
```
