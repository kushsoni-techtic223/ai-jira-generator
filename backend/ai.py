import json
import re
from typing import Any, Dict, List, Optional

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3"
MAX_CHUNKS = 4
CHUNK_SIZE = 4000
MAX_OLLAMA_CHUNK_CALLS = 3
OLLAMA_REQUEST_TIMEOUT = 180
LARGE_DOC_WORDS = 2000

PROMPT = """You are a senior software architect and product owner.

Read the ENTIRE requirement document and produce a COMPLETE Jira backlog grounded in the ACTUAL document scope.
Every description and task must reference specific features, clauses, integrations, or deliverables from the text.

RULES:
- Return ONLY valid JSON. No markdown.
- Top-level keys: project_name, modules, common_components
- Create MANY modules (8-20+) and MANY stories (minimum {min_stories} total stories)
- Each story needs 5-8 granular developer tasks tied to the document (not generic placeholders)
- NEVER use vague tasks like "Implement feature", "QA sign-off", "Analyze requirement" without naming what from the doc
- Descriptions must explain WHO needs WHAT and WHY using terms from the document (2-4 sentences)
- Story titles MUST be user stories: "As a [role] I can/want [specific feature from doc] so that [value]"
- Every task (frontend/backend/db) MUST mention the story feature name — no generic tasks detached from the story title
- common_components: array of 6-12 component NAME strings only

BAD task (do not use): "Implement login", "Add tests", "QA sign-off"
GOOD task: "Build email/password login form per wireframe with client-side validation rules from section 4.2"

Story schema (REQUIRED per story):
{{
  "title": "As a [role] I can [goal] so that [business value]",
  "description": "Context from the document",
  "acceptance_criteria": ["Given...", "When...", "Then..."],
  "priority": "High",
  "story_points": 5,
  "labels": ["backend", "api"],
  "frontend_tasks": ["5+ UI/React tasks for this story"],
  "backend_tasks": ["5+ API/service tasks for this story"],
  "db_tasks": ["4+ migration/schema tasks for this story"],
  "frontend_keys": {{ "routes": [], "components": [], "state_keys": [] }},
  "backend_keys": {{ "env": [], "api_endpoints": [], "middleware": [] }},
  "db_schema": {{
    "table": "table_name",
    "fields": [{{ "name": "id", "type": "uuid", "required": true, "key": "PRIMARY" }}],
    "required_keys": ["id", "created_at"]
  }},
  "tasks": ["combined checklist - optional"]
}}

Full output schema:
{{
  "project_name": "string",
  "modules": [{{ "name": "Module", "stories": [/* story schema */] }}],
  "common_components": ["PrimaryButton", "ApiClient", "Loader"]
}}

Requirement document:
"""

CHUNK_PROMPT = """You are extracting Jira backlog items from ONE section of a large requirements document.

Extract EVERY item in this section. Do not merge or skip items.
Minimum {min_stories} user stories for this section if the content supports it.

Write descriptions and tasks using EXACT terminology from this section (product names, APIs, SLAs, handover terms, payment flows, etc.).
Each task must mention a concrete deliverable from the section text.

Return ONLY JSON:
{{
  "modules": [{{ "name": "Module", "stories": [{{ "title": "", "description": "", "acceptance_criteria": [], "priority": "Medium", "story_points": 3, "labels": [], "tasks": [] }}] }}],
  "common_components": ["ComponentName"]
}}

Section text:
"""

EXPAND_PROMPT = """The backlog below is TOO SMALL for the document size.
Add MORE modules and stories for missing requirements. Keep existing items and ADD new ones.

Current backlog has {current_stories} stories. Target at least {min_stories} stories total.
Return the FULL merged JSON with project_name, modules, common_components.

Existing backlog:
{existing}

Remaining document text:
"""

RETRY_PROMPT = """Return ONLY valid JSON. Keys: project_name, modules, common_components.
Each story MUST have: title, description (2 sentences from document scope), tasks (5+ items referencing specific doc requirements).
No generic boilerplate. Minimum 15 stories. common_components: name strings only.

Document:
"""

SIMPLE_PROMPT = """Extract Jira backlog as JSON. Each story needs title, description, and 5 scope-specific tasks from the document:
{{"project_name":"","modules":[{{"name":"","stories":[{{"title":"","description":"","tasks":[]}}]}}],"common_components":[]}}

Document:
"""

# Patterns that indicate low-quality generic copy
_GENERIC_PATTERNS = [
    r"^implement\s+",
    r"^analyze\s+",
    r"^validate\s+",
    r"^design\s+",
    r"^document\s+",
    r"^qa\s+sign",
    r"^define acceptance criteria",
    r"covers requirements from the uploaded",
    r"as defined in the requirements document",
    r"^deliver\s+'",
    r"^break down\s+",
    r"^stakeholder review",
    r"^clarify requirement:",
    r"^peer review",
    r"^update project wiki",
]


# Set per request in generate_jira_data() for coerce/mapper paths
_DOC_TEXT: str = ""
_PROJECT_NAME: str = ""


def _set_doc_context(doc_text: str, project_name: str = "") -> None:
    global _DOC_TEXT, _PROJECT_NAME
    _DOC_TEXT = doc_text or ""
    _PROJECT_NAME = project_name or ""


def _fallback_payload(error: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "project_name": "AI Generated Project",
        "modules": [],
        "common_components": [],
    }
    if error:
        payload["error"] = error
    return payload


def _expected_min_stories(text: str) -> int:
    words = len(text.split())
    lines = len([ln for ln in text.splitlines() if len(ln.strip()) > 30])
    from_lines = max(15, lines)
    from_words = max(25, min(150, words // 70))
    return max(from_lines, from_words)


def _split_document(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []

    # Split on numbered sections, ALL CAPS headings, or double newlines
    parts = re.split(
        r"\n(?=\d+[\.\)]\s|[A-Z][A-Z0-9\s\-]{8,}\n|#{1,3}\s)",
        text,
    )
    parts = [p.strip() for p in parts if len(p.strip()) > 150]

    if len(parts) >= 2:
        return parts[:MAX_CHUNKS]

    # Fixed-size chunks with overlap
    chunks: List[str] = []
    start = 0
    overlap = 400
    while start < len(text) and len(chunks) < MAX_CHUNKS:
        end = min(start + CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap

    return chunks if chunks else [text[:20000]]


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").strip().title()


def _pascal_case(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _default_component_code(name: str, description: str = "") -> str:
    comp = _pascal_case(name) or "SharedComponent"
    desc = description or f"Reusable {comp} used across the application."
    return f'''import React from "react";

export type {comp}Props = {{
  className?: string;
  children?: React.ReactNode;
}};

/**
 * {desc}
 */
export function {comp}({{ className = "", children }}: {comp}Props) {{
  return (
    <div className={{`shared-{comp.lower()} ${{className}}`}} data-component="{comp}">
      {{children ?? <span>{comp}</span>}}
    </div>
  );
}}

export default {comp};
'''


def _normalize_component(item: Any) -> Dict[str, Any]:
    if isinstance(item, str):
        name = item.strip()
        return {
            "name": name,
            "description": f"Shared UI component: {name}",
            "file_path": f"src/components/shared/{_pascal_case(name)}.tsx",
            "language": "typescript",
            "code": _default_component_code(name),
        }

    if isinstance(item, dict):
        name = (item.get("name") or "SharedComponent").strip()
        description = (item.get("description") or f"Shared component: {name}").strip()
        code = item.get("code") or ""
        if isinstance(code, str):
            code = code.strip()
        if not code:
            code = _default_component_code(name, description)

        return {
            "name": name,
            "description": description,
            "file_path": (
                item.get("file_path")
                or f"src/components/shared/{_pascal_case(name)}.tsx"
            ).strip(),
            "language": (item.get("language") or "typescript").strip(),
            "code": code,
        }

    name = str(item).strip()
    return _normalize_component(name)


def _merge_components_list(components: List[Any]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in components:
        normalized = _normalize_component(item)
        key = normalized["name"].lower()
        if key and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out


def _is_generic_text(text: str) -> bool:
    if not text or len(text.strip()) < 12:
        return True
    lowered = text.strip().lower()
    for pattern in _GENERIC_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return True
    return False


def _extract_relevant_snippet(
    doc_text: str, title: str, module_name: str = "", max_len: int = 450
) -> str:
    if not doc_text:
        return ""

    query_words = set(
        re.findall(r"[a-zA-Z]{3,}", f"{title} {module_name}".lower())
    ) - {"the", "and", "for", "with", "from", "that", "this", "user", "can"}

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", doc_text) if len(p.strip()) > 40]
    if not paragraphs:
        paragraphs = [doc_text[i : i + 500] for i in range(0, min(len(doc_text), 3000), 500)]

    best_score = 0
    best_para = ""
    for para in paragraphs:
        para_lower = para.lower()
        score = sum(1 for w in query_words if w in para_lower)
        if score > best_score:
            best_score = score
            best_para = para

    if not best_para and paragraphs:
        best_para = paragraphs[0]

    return best_para[:max_len].strip()


def _extract_feature_phrase(text: str) -> str:
    clean = re.sub(r"^[\d\.\)\-\*•]+\s*", "", (text or "").strip())
    clean = re.sub(r"\s+", " ", clean)
    clean = re.sub(r"^(the|a|an)\s+", "", clean, flags=re.I)
    return clean.strip()


def _infer_user_role(module_name: str, text: str) -> str:
    combined = f"{module_name} {text}".lower()
    if any(k in combined for k in ("admin", "dashboard", "manage", "operator")):
        return "admin"
    if any(k in combined for k in ("developer", "handover", "repository", "devops")):
        return "developer"
    if any(k in combined for k in ("client", "customer", "buyer", "vendor")):
        return "customer"
    if any(k in combined for k in ("payment", "checkout", "billing")):
        return "customer"
    if any(k in combined for k in ("support", "sla", "warranty")):
        return "support engineer"
    return "user"


def _is_generic_title(title: str) -> bool:
    if not title or len(title.strip()) < 10:
        return True
    lowered = title.strip().lower()
    generic = (
        "requirement item",
        "review ",
        "implement ",
        "item ",
        "untitled",
        "story ",
        "general",
    )
    if any(lowered.startswith(g) or lowered == g.strip() for g in generic):
        return True
    if lowered.startswith("as a ") and len(title) > 30:
        return False
    # Title-case fragment without user story format
    if re.match(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){0,6}$", title.strip()):
        return True
    return False


def _normalize_story_title(
    raw: str,
    module_name: str = "",
    snippet: str = "",
    requirement_value: Any = None,
) -> str:
    """Turn document lines / keys into accurate user-story titles."""
    source = _extract_feature_phrase(raw)
    if isinstance(requirement_value, str) and requirement_value.strip():
        if len(requirement_value.strip()) > len(source):
            source = _extract_feature_phrase(requirement_value)
    if snippet and len(source) < 25:
        source = _extract_feature_phrase(snippet[:120])

    if not source:
        return f"As a user I can fulfill {module_name or 'project'} requirements"

    if re.match(r"^as a\s+", source, re.I) and len(source) > 25:
        return source[:180]

    role = _infer_user_role(module_name, source + " " + (snippet or ""))
    module = module_name or "system"

    # Short feature label e.g. "Payment Gateway Credentials"
    if len(source.split()) <= 10 and not source.endswith("."):
        feature = source[0].lower() + source[1:] if source else source
        return (
            f"As a {role} I want {feature} in {module} "
            f"so that the documented scope is delivered"
        )[:180]

    # Full sentence from document — trim and wrap
    sentence = source.rstrip(".")
    if len(sentence) > 100:
        sentence = sentence[:100].rsplit(" ", 1)[0]

    lowered = sentence.lower()
    if lowered.startswith(("provide ", "ensure ", "deliver ", "implement ", "support ", "enable ")):
        return (
            f"As a {role} I need the system to {lowered} "
            f"(module: {module})"
        )[:180]

    return (
        f"As a {role} I can access {sentence} "
        f"as specified for {module}"
    )[:180]


def _story_feature_label(
    title: str,
    snippet: str = "",
    raw_source: str = "",
) -> str:
    """Short feature name for tasks/API/DB — not the full user-story sentence."""
    for source in (raw_source, snippet, title):
        if not source or not str(source).strip():
            continue
        s = _extract_feature_phrase(str(source))
        if re.match(r"^as a\s+", s, re.I):
            match = re.search(
                r"i (?:want|need|can(?:\s+use|\s+access)?)\s+(.+?)"
                r"(?:\s+in\s+|\s+so that|\s*\(|$)",
                s,
                re.I,
            )
            if match:
                label = match.group(1).strip(" .")
                if label:
                    return label[:80]
            continue
        if 3 <= len(s.split()) <= 14:
            return s[:80]
    words = [w for w in _title_keywords(title) if len(w) > 3]
    if words:
        return " ".join(words[:6])[:80]
    return "feature"


def _title_keywords(title: str) -> set[str]:
    words = set(re.findall(r"[a-zA-Z]{3,}", title.lower()))
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "user", "can",
        "want", "need", "system", "documented", "scope", "module", "delivered",
        "specified", "requirements", "story", "fulfill", "project", "access",
    }
    return words - stop


def _task_mentions_story(task: str, title: str, feature_label: str = "") -> bool:
    if not task or not title:
        return False
    task_l = task.lower()
    feature = (feature_label or _story_feature_label(title)).lower()
    if feature and feature != "feature" and feature in task_l:
        return True
    keywords = _title_keywords(title) | _title_keywords(feature)
    if not keywords:
        return title.lower()[:20] in task_l
    hits = sum(1 for w in keywords if w in task_l)
    return hits >= max(1, min(2, len(keywords) // 3))


def _refine_task_for_story(
    task: str,
    title: str,
    layer: str,
    module_name: str,
    snippet: str,
    feature_label: str = "",
) -> str:
    task = (task or "").strip()
    feature = feature_label or _story_feature_label(title, snippet)
    short = feature[:70] if feature else title[:70]
    module = module_name or "system"
    ctx = f" (ref: {snippet[:70]}...)" if snippet and len(snippet) > 20 else ""

    if task and _task_mentions_story(task, title, feature) and not _is_generic_text(task):
        return task

    layer_label = {"frontend": "Frontend", "backend": "Backend", "db": "Database"}.get(
        layer, "Task"
    )

    if layer == "frontend":
        return (
            f"{layer_label}: Implement UI for \"{short}\" in {module} module"
            f"{ctx}"
        )
    if layer == "backend":
        return (
            f"{layer_label}: Build API/service logic for \"{short}\" in {module}"
            f"{ctx}"
        )
    return (
        f"{layer_label}: Create schema/migrations supporting \"{short}\" in {module}"
        f"{ctx}"
    )


def _refine_layer_task_list(
    tasks: List[str],
    title: str,
    layer: str,
    module_name: str,
    snippet: str,
    feature_label: str = "",
) -> List[str]:
    refined: List[str] = []
    seen: set[str] = set()
    for task in tasks or []:
        out = _refine_task_for_story(
            str(task), title, layer, module_name, snippet, feature_label
        )
        key = out.lower()
        if key not in seen:
            seen.add(key)
            refined.append(out)
    return refined[:8]


def _scope_aware_description(
    title: str,
    module_name: str,
    snippet: str,
    project_name: str = "",
    requirement_value: Any = None,
) -> str:
    proj = project_name or "the project"
    module = module_name or "this module"

    if snippet:
        intro = (
            f"Within {proj} ({module}), deliver \"{title}\" per the requirements: "
            f"{snippet[:320]}"
        )
    else:
        intro = (
            f"Within {proj} ({module}), deliver \"{title}\" as specified in the "
            f"uploaded requirements document."
        )

    if requirement_value is not None and not isinstance(requirement_value, bool):
        intro += f" Target/detail from document: {requirement_value}."

    return intro.strip() + " This story should be demo-ready for stakeholder review."


def _scope_aware_tasks(
    title: str,
    module_name: str,
    snippet: str,
    requirement_value: Any = None,
) -> List[str]:
    """Build tasks anchored to document language, not generic templates."""
    ctx = snippet or title
    kw = f"{title} {module_name} {ctx} {requirement_value or ''}".lower()
    tasks: List[str] = []

    tasks.append(
        f"Map \"{title}\" to exact clauses in the requirements doc and list impacted systems"
    )

    if snippet:
        tasks.append(
            f"Technical spike: confirm approach for \"{title}\" given: {snippet[:140]}..."
        )

    if isinstance(requirement_value, (int, float)):
        tasks.append(
            f"Implement \"{title}\" meeting documented target/value of {requirement_value} "
            f"with measurable verification"
        )
    elif isinstance(requirement_value, str) and requirement_value.strip():
        tasks.append(
            f"Configure \"{title}\" per spec: {requirement_value[:120]}"
        )

    if any(k in kw for k in ("payment", "gateway", "stripe", "billing", "checkout")):
        tasks.append(
            "Integrate payment gateway credentials, sandbox testing, and production cutover checklist"
        )
    if any(k in kw for k in ("auth", "login", "signup", "oauth", "session")):
        tasks.append(
            "Implement authentication flow (UI + API), session handling, and security review"
        )
    if any(k in kw for k in ("handover", "repository", "code", "access", "ownership")):
        tasks.append(
            "Prepare repo access, branch protections, env secrets handover, and README/runbook"
        )
    if any(k in kw for k in ("support", "sla", "warranty", "maintenance")):
        tasks.append(
            "Define post-go-live support process, escalation path, and SLA monitoring"
        )
    if any(k in kw for k in ("qa", "test", "quality", "uat")):
        tasks.append(
            "Author test plan + automated tests aligned to acceptance criteria for this scope"
        )
    if any(k in kw for k in ("api", "integration", "webhook", "endpoint")):
        tasks.append(
            "Define API contract, error handling, and integration tests with dependent services"
        )
    if any(k in kw for k in ("ui", "screen", "dashboard", "form", "mobile", "app")):
        tasks.append(
            "Build UI components and responsive layouts per requirement; hook to backend APIs"
        )
    if any(k in kw for k in ("deploy", "release", "ci", "cd", "pipeline")):
        tasks.append(
            "Set up CI/CD steps, staging validation, and release rollback plan for this deliverable"
        )

    tasks.append(
        f"Peer review + demo \"{title}\" with product owner using acceptance criteria sign-off"
    )
    tasks.append(
        f"Update project wiki/release notes documenting what was delivered for \"{title}\""
    )

    # Dedupe while preserving order
    seen: set[str] = set()
    unique: List[str] = []
    for t in tasks:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return _refine_layer_task_list(unique, title, "backend", module_name, snippet)


def _story_slug(title: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    return "_".join(words[:5]) or "feature"


def _pascal_from_slug(slug: str) -> str:
    return "".join(w.capitalize() for w in slug.split("_")) or "Feature"


def _build_layer_breakdown(
    title: str,
    module_name: str,
    snippet: str = "",
    feature_label: str = "",
) -> Dict[str, Any]:
    feature = feature_label or _story_feature_label(title, snippet)
    slug = _story_slug(feature)
    pascal = _pascal_from_slug(slug)
    entity = slug.split("_")[0] if slug else "item"
    table = f"{entity}s" if not entity.endswith("s") else entity
    route = f"/{slug.replace('_', '-')}"
    api_base = f"/api/v1/{slug.replace('_', '-')}"
    ctx = (snippet or feature)[:140]
    kw = f"{feature} {snippet} {module_name}".lower()
    mod = module_name or "system"

    frontend_tasks = [
        f"Frontend: Design screen flow for \"{feature}\" ({mod}) using requirement: {ctx[:85]}",
        f"Frontend: Add route {route} and {pascal}Page for \"{feature}\"",
        f"Frontend: Build {pascal}Form with field validation for \"{feature}\" business rules",
        f"Frontend: Connect {pascal} UI to backend APIs for \"{feature}\" with loading/error states",
        f"Frontend: Apply responsive layout + a11y for \"{feature}\" user journey",
        f"Frontend: Write UI tests covering happy/edge paths for \"{feature}\"",
    ]

    backend_tasks = [
        f"Backend: Publish OpenAPI for \"{feature}\" at {api_base} (GET/POST/PATCH)",
        f"Backend: Implement domain service for \"{feature}\" in {mod} with validation rules",
        f"Backend: Add secured controllers for \"{feature}\" ({api_base}) with auth middleware",
        f"Backend: Persist \"{feature}\" data via repository on table `{table}`",
        f"Backend: Add unit/integration tests for \"{feature}\" success and failure cases",
        f"Backend: Add structured logs/metrics for \"{feature}\" API operations",
    ]

    db_tasks = [
        f"Database: Model `{table}` for \"{feature}\" with PK/FK and status fields",
        f"Database: Write migration for \"{feature}\" including required keys and indexes",
        f"Database: Add query indexes for \"{feature}\" list/filter operations in {mod}",
        f"Database: Prepare seed/rollback data for \"{feature}\" QA scenarios",
        f"Database: Verify constraints for \"{feature}\" and document schema in wiki",
    ]

    if any(k in kw for k in ("payment", "gateway", "stripe", "billing")):
        backend_tasks.insert(
            2,
            f"Backend: Configure Stripe/payment env keys and webhook handler for \"{feature}\"",
        )
        db_tasks.insert(
            1,
            f"Database: Add `{table}` payment columns for \"{feature}\" (amount, currency, gateway_ref)",
        )

    if any(k in kw for k in ("auth", "login", "signup", "session", "jwt")):
        backend_tasks.insert(
            2,
            f"Backend: Implement JWT/session auth endpoints supporting \"{feature}\"",
        )
        db_tasks.insert(
            1,
            f"Database: Add auth columns on `{table}` for \"{feature}\" (email, password_hash, token)",
        )
        frontend_tasks.insert(
            2,
            f"Frontend: Build login/signup screens tied to \"{feature}\" with protected routes",
        )

    frontend_tasks = _refine_layer_task_list(
        frontend_tasks, title, "frontend", module_name, snippet, feature
    )
    backend_tasks = _refine_layer_task_list(
        backend_tasks, title, "backend", module_name, snippet, feature
    )
    db_tasks = _refine_layer_task_list(
        db_tasks, title, "db", module_name, snippet, feature
    )

    frontend_keys = {
        "routes": [route, f"{route}/:id"],
        "components": [f"{pascal}Page", f"{pascal}Form", f"{pascal}List"],
        "state_keys": [f"{slug}_loading", f"{slug}_error", f"{slug}_data"],
    }

    backend_keys = {
        "env": [
            "DATABASE_URL",
            "JWT_SECRET",
            f"{slug.upper()}_FEATURE_FLAG",
        ],
        "api_endpoints": [
            f"GET {api_base}  — list {feature}",
            f"POST {api_base} — create {feature}",
            f"GET {api_base}/{{id}} — read {feature}",
            f"PATCH {api_base}/{{id}} — update {feature}",
        ],
        "middleware": ["auth", "validation", "rate_limit"],
    }

    fields: List[Dict[str, Any]] = [
        {"name": "id", "type": "uuid", "required": True, "key": "PRIMARY"},
        {"name": f"{entity}_id", "type": "uuid", "required": True, "key": "FK"},
        {"name": "status", "type": "varchar(50)", "required": True, "key": "INDEX"},
        {"name": "created_at", "type": "timestamptz", "required": True, "key": "INDEX"},
        {"name": "updated_at", "type": "timestamptz", "required": True, "key": ""},
    ]

    if any(k in kw for k in ("payment", "gateway")):
        fields.extend(
            [
                {"name": "amount", "type": "decimal(12,2)", "required": True, "key": ""},
                {"name": "currency", "type": "varchar(3)", "required": True, "key": ""},
                {"name": "gateway_ref", "type": "varchar(255)", "required": True, "key": "UNIQUE"},
            ]
        )

    if any(k in kw for k in ("user", "auth", "login", "customer")):
        fields.extend(
            [
                {"name": "email", "type": "varchar(255)", "required": True, "key": "UNIQUE"},
                {"name": "password_hash", "type": "text", "required": False, "key": ""},
            ]
        )

    required_keys = [f["name"] for f in fields if f.get("required")]

    db_schema = {
        "table": table,
        "fields": fields,
        "required_keys": required_keys,
    }

    return {
        "frontend_tasks": frontend_tasks[:8],
        "backend_tasks": backend_tasks[:8],
        "db_tasks": db_tasks[:6],
        "frontend_keys": frontend_keys,
        "backend_keys": backend_keys,
        "db_schema": db_schema,
    }


def _merge_layer_tasks(story: Dict[str, Any]) -> List[str]:
    combined: List[str] = []
    for layer in ("frontend_tasks", "backend_tasks", "db_tasks"):
        for t in story.get(layer) or []:
            if isinstance(t, str) and t.strip() and t not in combined:
                combined.append(t.strip())
    for t in story.get("tasks") or []:
        if isinstance(t, str) and t.strip() and t not in combined:
            combined.append(t.strip())
    return combined


def _apply_layer_breakdown(
    story: Dict[str, Any],
    title: str,
    module_name: str,
    snippet: str,
    feature_label: str = "",
) -> Dict[str, Any]:
    feature = feature_label or _story_feature_label(
        title, snippet, story.get("_raw_source", "")
    )
    layers = _build_layer_breakdown(title, module_name, snippet, feature)

    for key in (
        "frontend_tasks",
        "backend_tasks",
        "db_tasks",
        "frontend_keys",
        "backend_keys",
        "db_schema",
    ):
        existing = story.get(key)
        if not existing or (isinstance(existing, list) and len(existing) == 0):
            story[key] = layers[key]
        elif key == "db_schema" and isinstance(existing, dict) and not existing.get("fields"):
            story[key] = layers[key]

    # Always refine layer tasks so they match the story title
    for layer_key, layer_name in (
        ("frontend_tasks", "frontend"),
        ("backend_tasks", "backend"),
        ("db_tasks", "db"),
    ):
        current = story.get(layer_key) or []
        if isinstance(current, list):
            story[layer_key] = _refine_layer_task_list(
                current if current else layers[layer_key],
                title,
                layer_name,
                module_name,
                snippet,
                feature,
            )
        else:
            story[layer_key] = layers[layer_key]

    story["tasks"] = _merge_layer_tasks(story)
    return story


def _value_to_tasks(
    label: str,
    value: Any,
    module_name: str = "",
    doc_text: str = "",
) -> List[str]:
    snippet = _extract_relevant_snippet(doc_text, label, module_name)
    return _scope_aware_tasks(label, module_name, snippet, value)


def _default_story(
    title: str,
    tasks: List[str],
    labels: Optional[List[str]] = None,
    module_name: str = "",
    doc_text: str = "",
    requirement_value: Any = None,
    project_name: str = "",
) -> Dict[str, Any]:
    snippet = _extract_relevant_snippet(doc_text, title, module_name)
    raw_source = _extract_feature_phrase(
        str(requirement_value) if requirement_value else title
    )
    if _is_generic_title(title) or not re.match(r"^as a\s+", title, re.I):
        title = _normalize_story_title(
            title, module_name, snippet, requirement_value
        )

    if not tasks or all(_is_generic_text(t) for t in tasks):
        tasks = _scope_aware_tasks(title, module_name, snippet, requirement_value)

    desc = _scope_aware_description(
        title, module_name, snippet, project_name, requirement_value
    )

    ac = [
        f"Given the documented requirement for {title}, prerequisites are in place",
        f"When frontend, backend, and DB tasks are complete, {title} works end-to-end",
        f"Then stakeholders approve {title} in demo and it is production-ready",
    ]
    if snippet:
        ac.insert(0, f"Requirement context: {snippet[:200]}...")

    base = {
        "title": title,
        "description": desc,
        "acceptance_criteria": ac,
        "priority": "Medium",
        "story_points": 5,
        "labels": labels or [module_name.lower().replace(" ", "-"), "requirements"],
        "tasks": tasks,
        "_raw_source": raw_source,
    }
    base = _apply_layer_breakdown(
        base, title, module_name, snippet, raw_source
    )
    base.pop("_raw_source", None)
    return base


def _dict_section_to_module(section_name: str, section: Dict[str, Any]) -> Dict[str, Any]:
    stories: List[Dict[str, Any]] = []
    for key, value in section.items():
        if isinstance(value, dict) and not isinstance(value, bool):
            for sub_key, sub_val in value.items():
                raw = _humanize_key(f"{key} — {sub_key}")
                title = _normalize_story_title(raw, section_name, str(sub_val), sub_val)
                stories.append(
                    _default_story(
                        title,
                        _value_to_tasks(title, sub_val, section_name, _DOC_TEXT),
                        [section_name.lower()],
                        module_name=section_name,
                        doc_text=_DOC_TEXT,
                        requirement_value=sub_val,
                        project_name=_PROJECT_NAME,
                    )
                )
        else:
            raw = _humanize_key(key)
            title = _normalize_story_title(raw, section_name, str(value), value)
            stories.append(
                _default_story(
                    title,
                    _value_to_tasks(title, value, section_name, _DOC_TEXT),
                    [section_name.lower()],
                    module_name=section_name,
                    doc_text=_DOC_TEXT,
                    requirement_value=value,
                    project_name=_PROJECT_NAME,
                )
            )

    if not stories:
        stories.append(
            _default_story(
                f"Review {section_name}",
                _value_to_tasks(section_name, True, section_name, _DOC_TEXT),
                module_name=section_name,
                doc_text=_DOC_TEXT,
                project_name=_PROJECT_NAME,
            )
        )

    return {"name": section_name, "stories": stories}


def _looks_like_wrong_schema(data: Dict[str, Any]) -> bool:
    wrong_keys = {"requirements", "post_implementation_support", "phases", "deliverables"}
    if wrong_keys.intersection(data.keys()):
        return True
    return "modules" not in data


def map_to_jira_format(data: Dict[str, Any]) -> Dict[str, Any]:
    modules: List[Dict[str, Any]] = []

    if isinstance(data.get("requirements"), dict):
        modules.append(_dict_section_to_module("Requirements", data["requirements"]))

    if isinstance(data.get("post_implementation_support"), dict):
        modules.append(
            _dict_section_to_module(
                "Post Implementation Support", data["post_implementation_support"]
            )
        )

    for key, value in data.items():
        if key in ("project_name", "modules", "common_components", "error"):
            continue
        if key in ("requirements", "post_implementation_support"):
            continue
        if isinstance(value, dict):
            modules.append(_dict_section_to_module(_humanize_key(key), value))

    default_names = ["SharedUI", "ApiClient", "ErrorHandler", "FormValidation", "AuthGuard"]
    raw_components = data.get("common_components")
    if isinstance(raw_components, list) and raw_components:
        components = _merge_components_list(raw_components)
    else:
        components = _merge_components_list(default_names)

    return {
        "project_name": data.get("project_name") or "Project from Document",
        "modules": modules,
        "common_components": components,
    }


def _count_stories(data: Dict[str, Any]) -> int:
    return sum(len(m.get("stories") or []) for m in data.get("modules") or [])


def _enrich_story(
    story: Dict[str, Any],
    module_name: str,
    idx: int,
    doc_text: str = "",
    project_name: str = "",
) -> Dict[str, Any]:
    raw_title = (story.get("title") or f"Story {idx}").strip()
    snippet = _extract_relevant_snippet(doc_text or _DOC_TEXT, raw_title, module_name)

    if _is_generic_title(raw_title):
        title = _normalize_story_title(
            raw_title, module_name, snippet, story.get("description")
        )
    elif not re.match(r"^as a\s+", raw_title, re.I):
        title = _normalize_story_title(raw_title, module_name, snippet)
    else:
        title = raw_title[:180]

    tasks = story.get("tasks") or []
    if not isinstance(tasks, list):
        tasks = [str(tasks)]
    tasks = [str(t).strip() for t in tasks if str(t).strip()]

    generic_count = sum(1 for t in tasks if _is_generic_text(t))
    if not tasks or generic_count >= max(1, len(tasks) // 2):
        tasks = _scope_aware_tasks(title, module_name, snippet)
    elif len(tasks) < 4:
        extra = _scope_aware_tasks(title, module_name, snippet)
        for t in extra:
            if t not in tasks and len(tasks) < 8:
                tasks.append(t)

    tasks = _refine_layer_task_list(tasks, title, "backend", module_name, snippet)

    description = (story.get("description") or "").strip()
    if _is_generic_text(description):
        description = _scope_aware_description(
            title, module_name, snippet, project_name or _PROJECT_NAME
        )

    ac = story.get("acceptance_criteria") or []
    if not isinstance(ac, list):
        ac = [str(ac)]
    ac = [str(a).strip() for a in ac if str(a).strip()]
    if not ac or all(_is_generic_text(a) for a in ac):
        ac = [
            f"Given documented scope for {title}, dependencies are ready",
            f"When implementation is complete, behaviour matches: {snippet[:120] or title}",
            f"Then PO/stakeholder signs off {title} in demo",
        ]

    labels = story.get("labels") or []
    if not isinstance(labels, list):
        labels = [str(labels)]
    labels = [str(l).strip() for l in labels if str(l).strip()]
    if not labels:
        labels = [module_name.lower().replace(" ", "-"), "backlog"]

    priority = story.get("priority") or "Medium"
    if priority not in ("Highest", "High", "Medium", "Low"):
        priority = "Medium"

    sp = story.get("story_points")
    try:
        sp = int(sp) if sp is not None else 5
    except (TypeError, ValueError):
        sp = 5
    sp = max(1, min(21, sp))

    enriched = {
        "id": story.get("id") or f"{module_name[:3].upper()}-{idx:03d}",
        "title": title,
        "description": description,
        "acceptance_criteria": ac,
        "priority": priority,
        "story_points": sp,
        "labels": labels,
        "tasks": tasks,
        "status": story.get("status") or "todo",
        "module_name": module_name,
        "source_snippet": snippet[:300] if snippet else None,
        "frontend_tasks": story.get("frontend_tasks") or [],
        "backend_tasks": story.get("backend_tasks") or [],
        "db_tasks": story.get("db_tasks") or [],
        "frontend_keys": story.get("frontend_keys") or {},
        "backend_keys": story.get("backend_keys") or {},
        "db_schema": story.get("db_schema") or {},
        "_raw_source": story.get("_raw_source") or raw_title,
    }
    enriched = _apply_layer_breakdown(
        enriched,
        title,
        module_name,
        snippet,
        enriched["_raw_source"],
    )
    enriched.pop("_raw_source", None)
    return enriched


def is_valid_jira_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    modules = data.get("modules")
    if not isinstance(modules, list) or len(modules) == 0:
        return False

    story_count = 0
    for mod in modules:
        if not isinstance(mod, dict) or not mod.get("name"):
            return False
        stories = mod.get("stories")
        if not isinstance(stories, list) or len(stories) == 0:
            return False
        for story in stories:
            if not isinstance(story, dict) or not story.get("title"):
                return False
            tasks = story.get("tasks")
            layer_tasks = (
                (story.get("frontend_tasks") or [])
                + (story.get("backend_tasks") or [])
                + (story.get("db_tasks") or [])
            )
            if (not isinstance(tasks, list) or len(tasks) == 0) and len(layer_tasks) == 0:
                return False
            story_count += 1

    return story_count > 0


def _merge_payloads(payloads: List[Dict[str, Any]], project_name: str = "") -> Dict[str, Any]:
    modules_map: Dict[str, Dict[str, Any]] = {}
    components_raw: List[Any] = []
    seen_stories: set[str] = set()

    for payload in payloads:
        if not payload:
            continue
        if not project_name and payload.get("project_name"):
            project_name = payload["project_name"]

        for comp in payload.get("common_components") or []:
            components_raw.append(comp)

        for mod in payload.get("modules") or []:
            if not isinstance(mod, dict):
                continue
            name = (mod.get("name") or "General").strip()
            key = name.lower()
            if key not in modules_map:
                modules_map[key] = {"name": name, "stories": []}

            for story in mod.get("stories") or []:
                if not isinstance(story, dict):
                    continue
                title_key = (story.get("title") or "").strip().lower()
                if title_key and title_key in seen_stories:
                    continue
                if title_key:
                    seen_stories.add(title_key)
                modules_map[key]["stories"].append(story)

    return {
        "project_name": project_name or "AI Generated Project",
        "modules": list(modules_map.values()),
        "common_components": _merge_components_list(components_raw),
    }


def normalize_jira_payload(
    data: Dict[str, Any], doc_text: str = ""
) -> Dict[str, Any]:
    project_name = (data.get("project_name") or _PROJECT_NAME or "Untitled Project").strip()
    doc = doc_text or _DOC_TEXT

    modules_out: List[Dict[str, Any]] = []
    story_idx = 1

    for mod in data.get("modules") or []:
        name = (mod.get("name") or "General").strip()
        stories_out = []
        for story in mod.get("stories") or []:
            if isinstance(story, dict):
                enriched = _enrich_story(story, name, story_idx, doc, project_name)
                stories_out.append(enriched)
                story_idx += 1
        if stories_out:
            modules_out.append({"name": name, "stories": stories_out})

    components = _merge_components_list(data.get("common_components") or [])
    if not components:
        components = _merge_components_list(
            ["Button", "Modal", "DataTable", "ApiClient", "FormValidator", "Loader"]
        )

    total_tasks = sum(
        len(s.get("frontend_tasks") or [])
        + len(s.get("backend_tasks") or [])
        + len(s.get("db_tasks") or [])
        for m in modules_out
        for s in m["stories"]
    )
    total_stories = sum(len(m["stories"]) for m in modules_out)

    return {
        "project_name": project_name,
        "modules": modules_out,
        "common_components": components,
        "stats": {
            "modules": len(modules_out),
            "stories": total_stories,
            "tasks": total_tasks,
            "components": len(components),
        },
    }


def _repair_json_string(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    if open_braces > 0:
        s += "}" * open_braces
    if open_brackets > 0:
        s += "]" * open_brackets
    return s


def _parse_json_response(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty response from model")

    candidates = [raw]

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, _repair_json_string(candidate)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    raise ValueError("Could not parse JSON from model output")


def _coerce_story(story: Any, module_name: str = "General") -> Optional[Dict[str, Any]]:
    if isinstance(story, str) and story.strip():
        title = story.strip()
        return _default_story(
            title,
            _value_to_tasks(title, True, module_name, _DOC_TEXT),
            [module_name.lower()],
            module_name=module_name,
            doc_text=_DOC_TEXT,
            project_name=_PROJECT_NAME,
        )

    if not isinstance(story, dict):
        return None

    title = (
        story.get("title")
        or story.get("name")
        or story.get("story")
        or story.get("summary")
        or story.get("user_story")
    )
    if not title:
        return None
    title = str(title).strip()

    tasks = (
        story.get("tasks")
        or story.get("subtasks")
        or story.get("sub_tasks")
        or story.get("items")
    )
    if isinstance(tasks, str):
        tasks = [tasks]
    elif isinstance(tasks, dict):
        tasks = list(tasks.values())
    elif not isinstance(tasks, list):
        tasks = []

    tasks = [str(t).strip() for t in tasks if str(t).strip()]
    if len(tasks) < 1:
        tasks = _value_to_tasks(title, True, module_name, _DOC_TEXT)

    out = dict(story)
    out["title"] = title
    out["tasks"] = tasks
    return out


def _coerce_module(mod: Any) -> Optional[Dict[str, Any]]:
    if isinstance(mod, str) and mod.strip():
        name = mod.strip()
        return {
            "name": name,
            "stories": [
                _default_story(
                    name,
                    _value_to_tasks(name, True, name, _DOC_TEXT),
                    module_name=name,
                    doc_text=_DOC_TEXT,
                    project_name=_PROJECT_NAME,
                )
            ],
        }

    if not isinstance(mod, dict):
        return None

    name = (mod.get("name") or mod.get("module") or mod.get("title") or "General").strip()
    raw_stories = mod.get("stories") or mod.get("issues") or mod.get("tickets") or []

    if isinstance(raw_stories, dict):
        raw_stories = [
            {
                "title": k,
                "tasks": _value_to_tasks(str(k), v, name, _DOC_TEXT),
            }
            for k, v in raw_stories.items()
        ]

    if not isinstance(raw_stories, list):
        raw_stories = []

    stories = []
    for s in raw_stories:
        coerced = _coerce_story(s, name)
        if coerced:
            stories.append(coerced)

    if not stories:
        return None

    return {"name": name, "stories": stories}


def _coerce_jira_payload(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"project_name": "AI Generated Project", "modules": [], "common_components": []}

    modules_out: List[Dict[str, Any]] = []
    for mod in data.get("modules") or []:
        coerced = _coerce_module(mod)
        if coerced:
            modules_out.append(coerced)

    # Flat list of stories at top level
    if not modules_out and isinstance(data.get("stories"), list):
        stories = []
        for s in data["stories"]:
            c = _coerce_story(s, "Backlog")
            if c:
                stories.append(c)
        if stories:
            modules_out.append({"name": "Backlog", "stories": stories})

    components = data.get("common_components") or data.get("components") or []
    if not isinstance(components, list):
        components = []

    return {
        "project_name": (data.get("project_name") or data.get("name") or "AI Generated Project"),
        "modules": modules_out,
        "common_components": components,
    }


def _call_ollama(prompt: str) -> str:
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"num_predict": 4096, "temperature": 0.2},
        },
        timeout=OLLAMA_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("response", "")


def _process_model_output(raw: str, doc_text: str = "") -> Dict[str, Any]:
    doc = doc_text or _DOC_TEXT
    parsed = _parse_json_response(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Model returned non-object JSON")

    coerced = _coerce_jira_payload(parsed)
    if is_valid_jira_payload(coerced):
        return normalize_jira_payload(coerced, doc)

    if is_valid_jira_payload(parsed):
        return normalize_jira_payload(parsed, doc)

    if _looks_like_wrong_schema(parsed) or _looks_like_wrong_schema(coerced):
        for source in (parsed, coerced):
            mapped = map_to_jira_format(source)
            if is_valid_jira_payload(mapped):
                return normalize_jira_payload(mapped, doc)

    # Last resort: map anything dict-like with content
    mapped = map_to_jira_format(parsed)
    if is_valid_jira_payload(mapped):
        return normalize_jira_payload(mapped, doc)

    raise ValueError(
        "Could not build backlog from model output. Try a shorter document or retry."
    )


def _generate_from_chunks(text: str, min_stories: int, doc_text: str = "") -> Dict[str, Any]:
    doc = doc_text or text
    chunks = _split_document(text)
    partials: List[Dict[str, Any]] = []
    word_count = len(text.split())

    if len(chunks) <= 1:
        prompt = PROMPT.format(min_stories=min(min_stories, 25)) + text[:15000]
        raw = _call_ollama(prompt)
        return _process_model_output(raw, doc)

    # Large docs: fewer Ollama calls; supplement fills the rest quickly
    chunk_limit = (
        MAX_OLLAMA_CHUNK_CALLS
        if word_count > LARGE_DOC_WORDS
        else min(len(chunks), MAX_CHUNKS)
    )
    per_chunk_min = max(4, min_stories // max(chunk_limit, 1))

    for chunk in chunks[:chunk_limit]:
        prompt = CHUNK_PROMPT.format(min_stories=per_chunk_min) + chunk
        try:
            raw = _call_ollama(prompt)
            parsed = _parse_json_response(raw)
            if parsed.get("modules"):
                partials.append(parsed)
        except Exception:
            continue

    if partials:
        merged = _merge_payloads(partials)
        coerced = _coerce_jira_payload(merged)
        if is_valid_jira_payload(coerced):
            return normalize_jira_payload(coerced, doc)
        if _looks_like_wrong_schema(merged) or _looks_like_wrong_schema(coerced):
            mapped = map_to_jira_format(merged)
            if is_valid_jira_payload(mapped):
                return normalize_jira_payload(mapped, doc)
        if _count_stories(coerced) > 0:
            return normalize_jira_payload(coerced, doc)

    # Chunk AI failed — build seed backlog from document (no extra Ollama wait)
    seed = _build_document_seed_backlog(text)
    return normalize_jira_payload(seed, doc)


def _build_document_seed_backlog(text: str) -> Dict[str, Any]:
    """Fast local backlog seed when Ollama chunk calls fail or time out."""
    project_name = _PROJECT_NAME or "Generated from Document"
    modules_map: Dict[str, List[Dict[str, Any]]] = {}
    current_module = "Document Requirements"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 20:
            continue
        if re.match(r"^[A-Z][A-Z0-9\s\-&]{8,}$", line):
            current_module = _humanize_key(line)
            continue
        if re.match(r"^#{1,3}\s", line):
            current_module = _humanize_key(re.sub(r"^#{1,3}\s*", "", line))
            continue
        if re.match(r"^\d+[\.\)]\s", line) or re.match(r"^[\-\*•]\s", line) or len(line) > 40:
            story = _line_to_story(line, current_module, project_name)
            modules_map.setdefault(current_module, []).append(story)

    if not modules_map:
        modules_map["Document Requirements"] = [
            _line_to_story(text[:500], "Document Requirements", project_name)
        ]

    return {
        "project_name": project_name,
        "modules": [{"name": k, "stories": v} for k, v in modules_map.items() if v],
        "common_components": ["Button", "Modal", "ApiClient", "Loader"],
    }


def _expand_backlog(text: str, current: Dict[str, Any], min_stories: int) -> Dict[str, Any]:
    existing_json = json.dumps(
        {
            "project_name": current.get("project_name"),
            "modules": current.get("modules"),
            "common_components": current.get("common_components"),
        },
        indent=0,
    )[:8000]

    remaining = text[12000:25000] if len(text) > 12000 else text[6000:12000]
    prompt = EXPAND_PROMPT.format(
        current_stories=_count_stories(current),
        min_stories=min_stories,
        existing=existing_json,
    ) + (remaining or text[:6000])

    raw = _call_ollama(prompt)
    expanded = _process_model_output(raw, text)
    merged = _merge_payloads([current, expanded], current.get("project_name", ""))
    return normalize_jira_payload(merged, text)


def _collect_existing_titles(data: Dict[str, Any]) -> set[str]:
    seen: set[str] = set()
    for mod in data.get("modules") or []:
        for story in mod.get("stories") or []:
            title = (story.get("title") or "").strip().lower()[:100]
            if title:
                seen.add(title)
    return seen


def _infer_priority_from_text(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("must", "critical", "mandatory", "blocking", "security")):
        return "Highest"
    if any(k in lowered for k in ("shall", "required", "payment", "auth", "deadline")):
        return "High"
    if any(k in lowered for k in ("should", "support", "optional", "nice")):
        return "Low"
    return "Medium"


def _line_to_story(line: str, module_name: str, project_name: str) -> Dict[str, Any]:
    clean = re.sub(r"^[\d\.\)\-\*•]+\s*", "", line.strip())
    clean = re.sub(r"\s+", " ", clean)
    title = _normalize_story_title(clean, module_name, clean)

    return _default_story(
        title,
        _value_to_tasks(title, clean, module_name, _DOC_TEXT),
        labels=[module_name.lower().replace(" ", "-"), "document-line"],
        module_name=module_name,
        doc_text=_DOC_TEXT,
        requirement_value=clean,
        project_name=project_name,
    )


def _supplement_backlog_from_document(
    text: str, result: Dict[str, Any], min_stories: int
) -> Dict[str, Any]:
    """Add stories from document lines/bullets not already covered by AI output."""
    seen = _collect_existing_titles(result)
    project_name = result.get("project_name") or _PROJECT_NAME or "Project"
    modules_map: Dict[str, List[Dict[str, Any]]] = {}

    for mod in result.get("modules") or []:
        name = (mod.get("name") or "General").strip()
        modules_map[name] = list(mod.get("stories") or [])

    current_module = "Document Requirements"
    candidates: List[tuple[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 20:
            continue

        if re.match(r"^[A-Z][A-Z0-9\s\-&]{8,}$", line):
            current_module = _humanize_key(line)
            continue

        if re.match(r"^#{1,3}\s", line):
            current_module = _humanize_key(re.sub(r"^#{1,3}\s*", "", line))
            continue

        if re.match(r"^\d+[\.\)]\s", line) or re.match(r"^[\-\*•]\s", line):
            candidates.append((current_module, line))
        elif len(line) > 45:
            candidates.append((current_module, line))

    added = 0
    for module_name, line in candidates:
        clean = re.sub(r"^[\d\.\)\-\*•]+\s*", "", line.strip()).lower()[:100]
        if not clean or clean in seen:
            continue
        # Skip near-duplicates
        if any(clean in s or s in clean for s in seen if len(s) > 10):
            continue

        seen.add(clean)
        story = _line_to_story(line, module_name, project_name)
        story["priority"] = _infer_priority_from_text(line)
        modules_map.setdefault(module_name, []).append(story)
        added += 1

    if added == 0 and _count_stories(result) < min_stories:
        # Split long paragraphs into sentence-level stories
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if len(para) < 80:
                continue
            for sentence in re.split(r"(?<=[.!?])\s+", para):
                sentence = sentence.strip()
                if len(sentence) < 35:
                    continue
                key = sentence.lower()[:100]
                if key in seen:
                    continue
                seen.add(key)
                story = _line_to_story(sentence, current_module, project_name)
                story["priority"] = _infer_priority_from_text(sentence)
                modules_map.setdefault(current_module, []).append(story)
                added += 1
                if _count_stories({"modules": [{"stories": s} for s in modules_map.values()]}) >= min_stories:
                    break
            if _count_stories({"modules": [{"stories": s} for s in modules_map.values()]}) >= min_stories:
                break

    merged = {
        "project_name": project_name,
        "modules": [{"name": k, "stories": v} for k, v in modules_map.items() if v],
        "common_components": result.get("common_components") or [],
    }
    return normalize_jira_payload(merged, text)


def _finalize_backlog(text: str, result: Dict[str, Any], min_stories: int) -> Dict[str, Any]:
    """Supplement locally; only call Ollama expand for small docs still missing stories."""
    if (
        _count_stories(result) < max(8, min_stories // 3)
        and len(text.split()) < LARGE_DOC_WORDS
    ):
        try:
            result = _expand_backlog(text, result, min_stories)
        except Exception:
            pass

    result = _supplement_backlog_from_document(text, result, min_stories)
    return result


def generate_jira_data(text: str) -> Dict[str, Any]:
    if not (text or "").strip():
        return _fallback_payload(
            "No text extracted from document. Try a different PDF/DOCX."
        )

    _set_doc_context(text)
    min_stories = _expected_min_stories(text)
    word_count = len(text.split())

    # Large documents: one Ollama pass + fast local supplement (avoids long timeouts)
    if word_count > LARGE_DOC_WORDS:
        try:
            prompt = PROMPT.format(min_stories=min(20, min_stories)) + text[:12000]
            raw = _call_ollama(prompt)
            result = _process_model_output(raw, text)
            _set_doc_context(text, result.get("project_name", ""))
            result = _supplement_backlog_from_document(text, result, min_stories)
            result["warning"] = (
                "Large document: fast mode (1 AI pass + document supplement)."
            )
            return result
        except Exception:
            seed = _build_document_seed_backlog(text)
            result = normalize_jira_payload(seed, text)
            result = _supplement_backlog_from_document(text, result, min_stories)
            result["warning"] = (
                "Ollama timed out or failed; backlog built from document structure."
            )
            return result

    try:
        result = _generate_from_chunks(text, min_stories, text)
        _set_doc_context(text, result.get("project_name", ""))
        result = _finalize_backlog(text, result, min_stories)
        return result

    except requests.exceptions.ConnectionError:
        return _fallback_payload(
            "Cannot connect to Ollama. Keep 'ollama run llama3' or the Ollama app running."
        )
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return _fallback_payload("Model 'llama3' not found. Run: ollama pull llama3")
        return _fallback_payload(f"Ollama error: {e}")
    except ValueError:
        pass
    except Exception as e:
        return _fallback_payload(f"Ollama request failed: {e}")

    for retry_prompt in (RETRY_PROMPT, SIMPLE_PROMPT):
        try:
            raw = _call_ollama(retry_prompt + text[:15000])
            result = _process_model_output(raw, text)
            _set_doc_context(text, result.get("project_name", ""))
            result = _finalize_backlog(text, result, min_stories)
            return result
        except ValueError:
            continue
        except requests.exceptions.ConnectionError:
            return _fallback_payload("Cannot connect to Ollama on retry.")
        except Exception:
            continue

    try:
        seed = _build_document_seed_backlog(text)
        result = normalize_jira_payload(seed, text)
        result = _supplement_backlog_from_document(text, result, min_stories)
        result["warning"] = (
            "AI returned invalid JSON; backlog was built from document headings."
        )
        return result
    except Exception:
        pass

    return _fallback_payload(
        "Could not parse AI response. Ensure Ollama is running and try again."
    )
