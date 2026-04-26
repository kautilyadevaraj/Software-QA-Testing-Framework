from __future__ import annotations

import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, Literal, TypedDict

from app.core.config import get_settings

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:
    QdrantClient = None
    models = None

logger = logging.getLogger(__name__)

ScenarioSource = Literal["agent_1", "agent_2", "manual"]

DEDUP_STOP_WORDS = {
    "a",
    "an",
    "and",
    "application",
    "app",
    "case",
    "cases",
    "check",
    "flow",
    "for",
    "from",
    "in",
    "new",
    "of",
    "on",
    "or",
    "page",
    "scenario",
    "scenarios",
    "system",
    "test",
    "testing",
    "the",
    "to",
    "through",
    "ui",
    "user",
    "users",
    "using",
    "validate",
    "verification",
    "verify",
    "web",
    "with",
}

DEDUP_SYNONYMS = {
    "add": "create",
    "added": "create",
    "adding": "create",
    "archive": "delete",
    "archived": "delete",
    "delete": "delete",
    "deleted": "delete",
    "deletion": "delete",
    "edit": "update",
    "edited": "update",
    "modify": "update",
    "modified": "update",
    "onboard": "register",
    "onboarding": "register",
    "register": "register",
    "registration": "register",
    "remove": "delete",
    "removed": "delete",
    "signup": "register",
    "sign": "register",
    "update": "update",
    "updated": "update",
    "view": "view",
    "list": "view",
    "search": "view",
}


class PreviewScenario(TypedDict):
    title: str
    description: str
    source: ScenarioSource


ScenarioType = Literal[
    "ALL",
    "HLS",
    "Functional",
    "Technical",
    "API",
    "Security",
    "Performance",
    "Integration",
    "Data",
    "Compliance",
    "Usability",
]
ScenarioAccessMode = Literal["UI_ONLY_WEB", "UI_AND_API", "TECHNICAL_REVIEW"]
ScenarioLevel = Literal["HLS", "DETAILED_HLS"]


class ScenarioGenerationOptions(TypedDict, total=False):
    max_scenarios: int | None
    scenario_types: list[ScenarioType]
    access_mode: ScenarioAccessMode
    scenario_level: ScenarioLevel


STRICT_JSON_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your entire response must be a raw JSON array only. "
    "No text before or after. No markdown. No backticks."
)

SHARED_HLS_GENERATION_INSTRUCTIONS = """System purpose:
This agent network generates high-level testing scenarios for human QA testers.
The scenarios are used to plan web application testing from ingested project documents and API documentation.

Source-of-truth rules:
- Infer the product domain, terminology, user roles, modules, workflows, business objects, rules, lifecycle states, permissions, and edge cases only from the provided input.
- Do not import outside domain knowledge, industry assumptions, hidden implementation details, or examples from prior conversations.
- Treat missing information as unknown. Do not invent features, roles, screens, data stores, integrations, or regulations.
- Use API documentation only as evidence of product capabilities and user-facing workflows; do not turn it into direct API test cases unless the selected tester access mode explicitly allows API-supported context.

Tester perspective:
- The primary audience is a tester validating a running web application.
- A good scenario must be interactable or observable by a tester through UI flows, visible messages, role-based behavior, files, dashboards, reports, notifications, or documented external behavior.
- Avoid scenarios that require source-code access, direct database access, backend shell access, internal service logs, or implementation inspection.

HLS quality bar:
- A high-level scenario is broader than a test case and narrower than a vague module name.
- Each scenario should express a complete tester intent: actor/context, action or workflow, business object, and observable outcome.
- Prefer meaningful end-to-end or lifecycle coverage over small field checks.
- Merge related operations into one scenario when they describe the same user journey.
- Keep titles concise and descriptions useful enough for a tester to understand why the scenario matters.

Output contract:
- Return only scenarios grounded in the provided input.
- Return only a raw JSON array.
- Each item must have exactly these keys: "title" and "description".
"""


def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    if QdrantClient is None:
        raise RuntimeError("qdrant-client is not installed")
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise RuntimeError("Qdrant credentials are not configured")
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60.0)


def ensure_category_payload_index(client: QdrantClient, collection_name: str) -> None:
    if models is None:
        raise RuntimeError("qdrant-client models are not available")

    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="category",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    except Exception as error:
        message = str(error).lower()
        if "already exists" in message or "already exist" in message:
            return
        logger.info("Qdrant category payload index setup skipped/failed for collection=%s: %s", collection_name, error)


def _is_missing_payload_index_error(error: Exception) -> bool:
    message = str(error).lower()
    return "index required" in message and "category" in message


def _payload_category_matches(payload: dict[str, Any], categories: set[str]) -> bool:
    category = str(payload.get("category") or "").strip().lower()
    return category in categories


def scroll_chunks(project_id: str, categories: list[str]) -> list[dict[str, Any]]:
    if models is None:
        raise RuntimeError("qdrant-client models are not available")

    client = get_qdrant_client()
    normalized_categories = {category.lower() for category in categories}
    ensure_category_payload_index(client, project_id)
    filter_ = models.Filter(
        must=[
            models.FieldCondition(
                key="category",
                match=models.MatchAny(any=categories),
            )
        ]
    )

    chunks: list[dict[str, Any]] = []
    next_offset: Any = None

    while True:
        try:
            points, next_offset = client.scroll(
                collection_name=project_id,
                scroll_filter=filter_,
                limit=100,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as error:
            if not _is_missing_payload_index_error(error):
                raise
            logger.warning(
                "Qdrant category index is unavailable for collection=%s; falling back to unfiltered scroll with local category filtering.",
                project_id,
            )
            return scroll_chunks_without_qdrant_filter(project_id, normalized_categories)

        for point in points:
            payload = getattr(point, "payload", None) or {}
            chunks.append(payload)
        if next_offset is None:
            break

    return sorted(chunks, key=lambda item: int(item.get("start_idx") or 0))


def scroll_chunks_without_qdrant_filter(project_id: str, categories: set[str]) -> list[dict[str, Any]]:
    client = get_qdrant_client()
    chunks: list[dict[str, Any]] = []
    next_offset: Any = None

    while True:
        points, next_offset = client.scroll(
            collection_name=project_id,
            limit=100,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = getattr(point, "payload", None) or {}
            if _payload_category_matches(payload, categories):
                chunks.append(payload)
        if next_offset is None:
            break

    return sorted(chunks, key=lambda item: int(item.get("start_idx") or 0))


def concatenate_chunk_text(chunks: list[dict[str, Any]]) -> str:
    return "\n\n".join(str(chunk.get("text") or "").strip() for chunk in chunks if str(chunk.get("text") or "").strip())


def _compact_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def build_text_batches(
    fragments: list[str],
    *,
    max_chars: int | None = None,
    max_items: int | None = None,
) -> list[str]:
    settings = get_settings()
    max_chars = max_chars or settings.scenario_agent_batch_chars
    max_items = max_items or settings.scenario_agent_batch_size

    batches: list[str] = []
    current: list[str] = []
    current_chars = 0

    for fragment in fragments:
        text = _compact_text(fragment)
        if not text:
            continue
        if len(text) > max_chars:
            text = f"{text[:max_chars].rstrip()}\n[truncated to stay within model/API limits]"

        next_chars = current_chars + len(text) + 2
        if current and (next_chars > max_chars or len(current) >= max_items):
            batches.append("\n\n".join(current))
            current = []
            current_chars = 0

        current.append(text)
        current_chars += len(text) + 2

    if current:
        batches.append("\n\n".join(current))

    return batches


def build_chunk_text_batches(chunks: list[dict[str, Any]]) -> list[str]:
    fragments: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        category = str(chunk.get("category") or "document").strip() or "document"
        start_idx = chunk.get("start_idx")
        end_idx = chunk.get("end_idx")
        location = ""
        if start_idx is not None and end_idx is not None:
            location = f", chars {start_idx}-{end_idx}"
        fragments.append(f"Chunk {index} ({category}{location}):\n{text}")
    return build_text_batches(fragments)


def _raw_llm_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "\n".join(str(item) for item in content)
    return str(content)


def _load_llm(api_key: str):
    settings = get_settings()
    os.environ["GROQ_API_KEY"] = api_key
    from langchain_groq import ChatGroq

    return ChatGroq(
        model=settings.groq_model,
        temperature=0,
        api_key=api_key,
        max_tokens=settings.groq_max_tokens,
        max_retries=0,
    )


def parse_json_array(raw: str) -> list[dict[str, Any]]:
    cleaned = raw.strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("LLM response was not a JSON array")
    return [item for item in parsed if isinstance(item, dict)]


def normalize_scenarios(items: list[dict[str, Any]], source: ScenarioSource | None = None) -> list[PreviewScenario]:
    scenarios: list[PreviewScenario] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        item_source = source or str(item.get("source") or "").strip()
        if not title or item_source not in {"agent_1", "agent_2", "manual"}:
            continue
        scenarios.append(
            {
                "title": title,
                "description": description,
                "source": item_source,  # type: ignore[typeddict-item]
            }
        )
    return scenarios


def scenario_types_instruction(scenario_types: list[str] | None) -> str:
    selected = [item for item in (scenario_types or []) if item and item not in {"ALL", "HLS"}]
    wants_hls = any(item == "HLS" for item in (scenario_types or []))
    hls_instruction = (
        "Every output must be a high-level scenario, not a detailed test case or checklist. "
        if wants_hls
        else ""
    )
    if not selected:
        return (
            hls_instruction
            + "Generate a balanced mix of functional, technical, API, security, performance, "
            "integration, data, compliance, and usability high-level scenarios when the documents support them."
        )
    return (
        hls_instruction
        + "Generate only these high-level scenario types: "
        + ", ".join(selected)
        + ". Ignore other scenario types unless they are necessary to explain the selected tester intent."
    )


def access_mode_instruction(access_mode: str | None) -> str:
    if access_mode == "UI_AND_API":
        return (
            "Assume testers primarily validate through the web UI, but may also use documented API behavior "
            "as supporting context. Do not require source-code access or direct database access."
        )
    if access_mode == "TECHNICAL_REVIEW":
        return (
            "Generate technically oriented high-level scenarios only when they can be validated through observable "
            "application behavior, logs exposed to testers, documented APIs, or admin screens. Avoid requiring code changes."
        )
    return (
        "Assume testers have access only to the running web application UI. They do not have source-code access, "
        "direct database access, backend shell access, or internal service logs. Generate only scenarios testable "
        "through visible UI flows, user roles, uploaded/downloaded files, messages, dashboards, and observable outcomes."
    )


def scenario_level_instruction(scenario_level: str | None) -> str:
    if scenario_level == "DETAILED_HLS":
        return (
            "Use complete high-level scenario intents with enough business context to test, but do not provide step-by-step actions."
        )
    return (
        "Use HLS format: one broad tester-executable intent per item. Avoid low-level validations, button-by-button steps, "
        "database checks, implementation details, and generic category names without a testable outcome."
    )


def existing_scenarios_instruction(existing_scenarios: list[PreviewScenario] | None) -> str:
    if not existing_scenarios:
        return "No existing scenarios were provided. Generate new scenarios from the current input."

    lines = [
        "Already covered scenarios:",
        "Do not generate scenarios that duplicate or rephrase these tester intents. Generate only additional coverage.",
    ]
    max_chars = 3500
    current_chars = sum(len(line) for line in lines)

    for index, scenario in enumerate(existing_scenarios, start=1):
        title = _compact_text(str(scenario.get("title") or ""))
        description = _compact_text(str(scenario.get("description") or ""))
        if not title:
            continue
        line = f"{index}. {title}"
        if description:
            line += f" - {description[:220]}"
        if current_chars + len(line) > max_chars:
            lines.append("- Additional existing scenarios omitted to keep the prompt within model limits.")
            break
        lines.append(line)
        current_chars += len(line)

    return "\n".join(lines)


def limit_scenarios(scenarios: list[PreviewScenario], max_scenarios: int | None) -> list[PreviewScenario]:
    if max_scenarios is None:
        return scenarios
    return scenarios[:max_scenarios]


def invoke_json_scenarios(prompt: str, agent_name: str, source: ScenarioSource | None = None) -> list[PreviewScenario]:
    settings = get_settings()
    api_keys = settings.groq_api_keys
    if not api_keys:
        raise RuntimeError("GROQ_API_KEY is not configured")

    last_error: Exception | None = None
    for key_index, api_key in enumerate(api_keys, start=1):
        llm = _load_llm(api_key)
        try:
            response = llm.invoke(prompt)
            return normalize_scenarios(parse_json_array(_raw_llm_content(response)), source=source)
        except (json.JSONDecodeError, ValueError) as first_error:
            logger.warning(
                "%s returned invalid JSON on key %s/%s; retrying with stricter JSON instruction: %s",
                agent_name,
                key_index,
                len(api_keys),
                first_error,
            )
            last_error = first_error
        except Exception as first_error:
            logger.warning("%s failed on Groq key %s/%s: %s", agent_name, key_index, len(api_keys), first_error)
            last_error = first_error
            continue

        try:
            response = llm.invoke(prompt + STRICT_JSON_RETRY_SUFFIX)
            return normalize_scenarios(parse_json_array(_raw_llm_content(response)), source=source)
        except (json.JSONDecodeError, ValueError) as retry_error:
            logger.warning("%s returned invalid JSON after retry on key %s/%s: %s", agent_name, key_index, len(api_keys), retry_error)
            last_error = retry_error
        except Exception as retry_error:
            logger.warning("%s failed after JSON retry on Groq key %s/%s: %s", agent_name, key_index, len(api_keys), retry_error)
            last_error = retry_error

    assert last_error is not None
    logger.error("%s failed with all configured Groq API keys: %s", agent_name, last_error)
    raise last_error


def generate_scenarios_from_batches(
    prompt_template: str,
    text_batches: list[str],
    *,
    agent_name: str,
    source: ScenarioSource,
    max_scenarios: int | None = None,
    scenario_types: list[str] | None = None,
    access_mode: str | None = None,
    scenario_level: str | None = None,
    existing_scenarios: list[PreviewScenario] | None = None,
) -> list[PreviewScenario]:
    settings = get_settings()
    scenarios: list[PreviewScenario] = []
    per_batch_limit = settings.scenario_agent_max_scenarios_per_batch
    candidate_limit = max_scenarios * 2 if max_scenarios is not None else None
    if max_scenarios is not None:
        per_batch_limit = max(1, min(per_batch_limit, max_scenarios))

    for batch_index, document_text in enumerate(text_batches, start=1):
        try:
            batch_scenarios = invoke_json_scenarios(
                prompt_template.format(
                    document_text=document_text,
                    max_scenarios=per_batch_limit,
                    shared_generation_instruction=SHARED_HLS_GENERATION_INSTRUCTIONS,
                    scenario_type_instruction=scenario_types_instruction(scenario_types),
                    access_mode_instruction=access_mode_instruction(access_mode),
                    scenario_level_instruction=scenario_level_instruction(scenario_level),
                    existing_scenarios_instruction=existing_scenarios_instruction(existing_scenarios),
                ),
                agent_name=f"{agent_name}_batch_{batch_index}",
                source=source,
            )
            scenarios.extend(batch_scenarios)
            scenarios = limit_scenarios(deduplicate_scenarios(scenarios), candidate_limit)
            if batch_index < len(text_batches) and settings.scenario_agent_batch_delay_seconds > 0:
                time.sleep(settings.scenario_agent_batch_delay_seconds)
        except Exception as error:
            logger.exception("%s skipped batch %s/%s after LLM failure: %s", agent_name, batch_index, len(text_batches), error)

    return limit_scenarios(filter_new_scenarios(deduplicate_scenarios(scenarios), existing_scenarios or []), max_scenarios)


def _scenario_fingerprint(scenario: PreviewScenario) -> str:
    return " ".join(_scenario_tokens(scenario))[:180]


def _canonical_word(word: str) -> str:
    return DEDUP_SYNONYMS.get(word, word)


def _scenario_tokens(scenario: PreviewScenario) -> list[str]:
    text = f"{scenario.get('title', '')} {scenario.get('description', '')}".lower()
    words = re.findall(r"[a-z0-9]+", text)
    return [
        _canonical_word(word)
        for word in words
        if len(word) > 2 and word not in DEDUP_STOP_WORDS
    ]


def _title_fingerprint(scenario: PreviewScenario) -> str:
    title = str(scenario.get("title") or "").lower()
    words = re.findall(r"[a-z0-9]+", title)
    return " ".join(sorted({
        _canonical_word(word)
        for word in words
        if len(word) > 2 and word not in DEDUP_STOP_WORDS
    }))


def _token_similarity(first: PreviewScenario, second: PreviewScenario) -> float:
    first_tokens = set(_scenario_tokens(first))
    second_tokens = set(_scenario_tokens(second))
    if not first_tokens or not second_tokens:
        return 0.0
    overlap = len(first_tokens & second_tokens)
    return overlap / len(first_tokens | second_tokens)


def _sequence_similarity(first: str, second: str) -> float:
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def _source_priority(source: ScenarioSource) -> int:
    return {"manual": 0, "agent_1": 1, "agent_2": 2}.get(source, 3)


def _prefer_scenario(first: PreviewScenario, second: PreviewScenario) -> PreviewScenario:
    first_priority = _source_priority(first["source"])
    second_priority = _source_priority(second["source"])
    if first_priority != second_priority:
        return first if first_priority < second_priority else second
    first_detail = len(first.get("description") or "") + len(first.get("title") or "")
    second_detail = len(second.get("description") or "") + len(second.get("title") or "")
    return first if first_detail >= second_detail else second


def _is_duplicate_scenario(first: PreviewScenario, second: PreviewScenario) -> bool:
    first_title = _title_fingerprint(first)
    second_title = _title_fingerprint(second)
    if first_title and second_title and first_title == second_title:
        return True

    title_similarity = _sequence_similarity(first_title, second_title)
    token_similarity = _token_similarity(first, second)
    full_similarity = _sequence_similarity(_scenario_fingerprint(first), _scenario_fingerprint(second))

    if title_similarity >= 0.9:
        return True
    if token_similarity >= 0.74 and title_similarity >= 0.58:
        return True
    return token_similarity >= 0.82 or full_similarity >= 0.88


def deduplicate_scenarios(scenarios: list[PreviewScenario]) -> list[PreviewScenario]:
    unique: list[PreviewScenario] = []
    for scenario in scenarios:
        key = _scenario_fingerprint(scenario)
        if not key:
            continue
        duplicate_index: int | None = None
        for index, existing in enumerate(unique):
            if _is_duplicate_scenario(existing, scenario):
                duplicate_index = index
                break

        if duplicate_index is None:
            unique.append(scenario)
        else:
            unique[duplicate_index] = _prefer_scenario(unique[duplicate_index], scenario)

    return unique


def filter_new_scenarios(
    scenarios: list[PreviewScenario],
    existing_scenarios: list[PreviewScenario],
) -> list[PreviewScenario]:
    if not existing_scenarios:
        return scenarios

    existing_unique = deduplicate_scenarios(existing_scenarios)
    new_items: list[PreviewScenario] = []
    for scenario in scenarios:
        if any(_is_duplicate_scenario(existing, scenario) for existing in existing_unique):
            continue
        new_items.append(scenario)
    return new_items
