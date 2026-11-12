"""Veille hebdomadaire sur les déchets textiles : Europe, France, Normandie.

Le workflow collecte les actualités des 7 derniers jours par recherche web,
les synthétise en un rapport en français, puis le publie dans une
bibliothèque de documents Mistral si un identifiant de bibliothèque est fourni.
"""

import io
import json
import os
from datetime import timedelta
from typing import Any

import pydantic
import mistralai.workflows as workflows
from mistralai.client import Mistral
from mistralai.client.models import File, WebSearchTool

RESEARCH_MODEL = "mistral-medium-latest"
REPORT_MODEL = "mistral-medium-latest"

SCOPES: dict[str, str] = {
    "europe": (
        "Actualités européennes (Union européenne) sur les déchets textiles : "
        "directive-cadre déchets et REP textile au niveau UE, stratégie pour des "
        "textiles durables et circulaires, règlement ESPR (écoconception, passeport "
        "numérique produit), collecte séparée obligatoire depuis le 1er janvier 2025, "
        "ReHubs et projets industriels de recyclage fibre-à-fibre, export de déchets "
        "textiles hors UE. Sources à privilégier : environment.ec.europa.eu, eea.europa.eu, "
        "europarl.europa.eu, consilium.europa.eu, euractiv.com, endseurope.com, "
        "reuters.com, fashionunited.eu, rehubs.eu."
    ),
    "france": (
        "Actualités françaises sur les déchets textiles : filière REP TLC et Refashion, "
        "refondation de la filière et cahiers des charges des éco-organismes, loi visant "
        "à réduire l'impact environnemental de la fast fashion, soutiens aux opérateurs "
        "de tri, collecte, tri, réemploi et recyclage en France, affichage environnemental, "
        "bonus réparation, projets industriels de recyclage en France. Sources à privilégier : "
        "ecologie.gouv.fr, legifrance.gouv.fr, senat.fr, assemblee-nationale.fr, "
        "ademe.fr, refashion.fr, banquedesterritoires.fr, fashionnetwork.com, "
        "fashionunited.fr, lesechos.fr, actu-environnement.com, nextwaste.fr, recy.net."
    ),
    "normandie": (
        "Actualités normandes sur les déchets textiles : actions de l'ADEME Normandie et du "
        "Comité régional de l'économie circulaire, Région Normandie et SRADDET, DREAL Normandie, "
        "plateforme NECI, acteurs locaux de collecte, tri et recyclage (GEBETEX Tri Normandie à "
        "Vernon, Le Relais, ressourceries, recycleries, associations d'insertion), collectivités "
        "normandes (Caen la mer, Métropole Rouen Normandie, Le Havre Seine Métropole), filière lin "
        "et fibres naturelles normandes, opérations de collecte et campagnes locales. Sources à "
        "privilégier : normandie.ademe.fr, neci.normandie.fr, normandie.fr, normandinamik.cci.fr, "
        "obdec-normandie.org, ouest-france.fr, paris-normandie.fr, tendanceouest.com, "
        "agriculteur-normand.com, union-agricole.fr."
    ),
}

RESEARCH_PROMPT = """Tu es un analyste spécialisé de la gestion des déchets textiles.

À l'aide de la recherche web, identifie les actualités publiées entre {period_start} et {period_end} \
sur les déchets textiles pour le périmètre suivant : {scope_label}. {scope_hints}

Consignes :
- Cherche activement (plusieurs requêtes) : réglementation et politiques publiques, filière de \
collecte/tri/recyclage/réemploi, acteurs et projets industriels, innovation et recherche, appels à \
projets et événements, études et chiffres clés.
- Ne retiens que des actualités des 7 derniers jours (entre {period_start} et {period_end}). \
Une actualité plus ancienne n'est acceptable que si une information nouvelle majeure la concerne.
- Ignore les pages commerciales, les doublons et les contenus sans substance.
- Résume chaque actualité en 2 à 3 phrases factuelles en français, en citant les chiffres clés.

Réponds UNIQUEMENT avec un tableau JSON (aucun texte autour) où chaque élément a les clés : \
"title", "date" (AAAA-MM-JJ si connue, sinon null), "source" (nom du média ou site), "url", \
"summary", "topics" (liste parmi : reglementation, filiere, innovation, financements, evenement, donnees).
Si aucune actualité n'est trouvée, réponds avec un tableau vide []."""

SYNTHESIS_PROMPT = """Tu es un analyste senior de l'économie circulaire des textiles.

Rédige en français un rapport de veille hebdomadaire en Markdown à partir des actualités collectées \
entre {period_start} et {period_end} et fournies ci-dessous au format JSON.

Structure attendue :
# Veille déchets textiles — semaine du {period_start} au {period_end}

## L'essentiel de la semaine
3 à 5 puces synthétiques reprenant les faits marquants tous périmètres confondus.

## Europe
## France
## Normandie
Pour chaque section : une introduction d'une phrase sur l'actualité du périmètre, puis une sous-section \
par actualité sous la forme `### [Titre](url)` suivie du résumé, de la source et de la date. \
Si aucune actualité n'a été trouvée pour un périmètre, écris une phrase l'indiquant explicitement.

## À suivre
Échéances réglementaires, événements et signaux à surveiller dans les semaines qui viennent.

## Sources
Liste des sources citées avec leurs liens.

Règles : reste strictement factuel, ne rien inventer, reprendre les chiffres des actualités, \
garder les liens sources. Ne renvoie QUE le Markdown du rapport."""

PERIOD_DAYS_FALLBACK = 7


def get_mistral_client() -> Mistral:
    return Mistral()


class ReportInput(pydantic.BaseModel):
    weeks_back: int = 1
    library_id: str | None = None
    dry_run: bool = False


class ReportOutput(pydantic.BaseModel):
    period_start: str
    period_end: str
    items_count: int
    report_markdown: str
    library_document_id: str | None = None


def _extract_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("Réponse Mistral vide")
    message = choices[0].message
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(getattr(chunk, "text", "") for chunk in content)


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    parsed = json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, list) else []


@workflows.activity(start_to_close_timeout=timedelta(minutes=15), retry_policy_max_attempts=3)
async def research_scope(scope: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    """Collecte les actualités d'un périmètre par recherche web."""
    scope_label = scope.capitalize()
    scope_hints = SCOPES.get(scope)
    if scope_hints is None:
        raise ValueError(f"Périmètre inconnu : {scope}")
    prompt = RESEARCH_PROMPT.format(
        period_start=period_start,
        period_end=period_end,
        scope_label=scope_label,
        scope_hints=scope_hints,
    )
    client = get_mistral_client()
    response = await client.chat.complete_async(
        model=RESEARCH_MODEL,
        messages=[{"role": "user", "content": prompt}],
        tools=[WebSearchTool()],
        temperature=0.2,
    )
    return _parse_json_array(_extract_text(response))


@workflows.activity(start_to_close_timeout=timedelta(minutes=10), retry_policy_max_attempts=3)
async def synthesize_report(
    items: list[dict[str, Any]], period_start: str, period_end: str
) -> str:
    """Synthétise les actualités collectées en un rapport Markdown en français."""
    prompt = SYNTHESIS_PROMPT.format(period_start=period_start, period_end=period_end)
    prompt += f"\n\nActualités collectées (JSON) :\n{json.dumps(items, ensure_ascii=False, indent=2)}"
    client = get_mistral_client()
    response = await client.chat.complete_async(
        model=REPORT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return _extract_text(response).strip()


@workflows.activity(start_to_close_timeout=timedelta(minutes=10), retry_policy_max_attempts=2)
async def publish_report(markdown: str, filename: str, library_id: str | None) -> str | None:
    """Publie le rapport dans une bibliothèque de documents Mistral."""
    target_library = library_id or os.environ.get("REPORT_LIBRARY_ID") or None
    if not target_library:
        return None
    client = get_mistral_client()
    document = await client.beta.libraries.documents.upload_async(
        library_id=target_library,
        file=File(fileName=filename, content=io.BytesIO(markdown.encode("utf-8"))),
    )
    return document.id


@workflows.workflow.define(
    name="textile-waste-weekly-report",
    workflow_display_name="Veille hebdomadaire déchets textiles",
    workflow_description=(
        "Rapport hebdomadaire sur les déchets textiles en Europe, en France et en "
        "Normandie : recherche web des actualités de la semaine, synthèse en français "
        "et publication dans une bibliothèque de documents Mistral."
    ),
)
class TextileWasteReportWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: ReportInput) -> ReportOutput:
        end = workflows.now()
        start = end - timedelta(days=7 * max(1, input.weeks_back))
        period_start = start.date().isoformat()
        period_end = end.date().isoformat()

        per_scope = await workflows.execute_activities_in_parallel(
            research_scope,
            items=list(SCOPES),
            extra_params={"period_start": period_start, "period_end": period_end},
        )
        items = [item for scope_items in per_scope or [] for item in scope_items]

        report_markdown = await synthesize_report(items, period_start, period_end)

        document_id: str | None = None
        if not input.dry_run:
            document_id = await publish_report(
                report_markdown,
                f"veille-dechets-textiles-{period_start}.md",
                input.library_id,
            )

        return ReportOutput(
            period_start=period_start,
            period_end=period_end,
            items_count=len(items),
            report_markdown=report_markdown,
            library_document_id=document_id,
        )
