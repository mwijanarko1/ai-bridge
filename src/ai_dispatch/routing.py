from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .adapters import normalize_target
from .jobs import CONFIG_DIR, PRIMARY_AGENTS

CATEGORIES = (
    "simple_edit",
    "implementation",
    "debugging",
    "refactor",
    "research",
    "review",
    "unknown",
)

DEFAULT_SCORES = {
    "opencode": {
        "simple_edit": 10,
        "implementation": 8,
        "debugging": 5,
        "refactor": 6,
        "research": 4,
        "review": 3,
    },
    "cursor": {
        "simple_edit": 6,
        "implementation": 9,
        "debugging": 10,
        "refactor": 9,
        "research": 5,
        "review": 4,
    },
    "claude": {
        "simple_edit": 6,
        "implementation": 7,
        "debugging": 8,
        "refactor": 7,
        "research": 7,
        "review": 6,
    },
    "codex": {
        "simple_edit": 7,
        "implementation": 8,
        "debugging": 8,
        "refactor": 8,
        "research": 8,
        "review": 10,
    },
}

CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "review": (r"\breview\b", r"\baudit\b", r"\binspect\b", r"\bcode review\b"),
    "research": (r"\bresearch\b", r"\bcompare\b", r"\bsummarize\b", r"\bexplain\b"),
    "refactor": (r"\brefactor\b", r"\brestructure\b", r"\bsplit\b", r"\brename across\b"),
    "debugging": (
        r"\bdebug\b",
        r"\bbug\b",
        r"\bfailing\b",
        r"\btrace\b",
        r"\brace condition\b",
        r"\bfix\b",
    ),
    "simple_edit": (r"\brename\b", r"\btoggle\b", r"\bsmall\b", r"\bdocs?\b", r"\bcopy edit\b"),
    "implementation": (r"\badd\b", r"\bimplement\b", r"\bbuild\b", r"\bcreate\b"),
}

HARD_PATTERNS = (
    r"\bmulti-file\b",
    r"\bmulti step\b",
    r"\bmigration\b",
    r"\bconcurrency\b",
    r"\bsecurity\b",
    r"\bdistributed\b",
    r"\bperformance\b",
    r"\barchitecture\b",
    r"\brefactor\b",
    r"\brace condition\b",
    r"\bseveral modules\b",
    r"\b[3-9]\s+files?\b",
)


def routing_config_path(cwd: str | None) -> Path | None:
    candidates: list[Path] = []
    if cwd:
        candidates.append(Path(cwd) / ".ai-bridge" / "routing.json")
    candidates.append(CONFIG_DIR / "routing.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_routing_config(cwd: str | None) -> dict[str, Any]:
    path = routing_config_path(cwd)
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid routing config at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid routing config at {path}: top-level JSON object required.")
    return payload


def classify_task(task: str, requested_difficulty: str = "auto") -> dict[str, Any]:
    lowered = str(task or "").lower()
    category_hits: dict[str, int] = {name: 0 for name in CATEGORY_PATTERNS}
    signals: list[str] = []

    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, lowered):
                category_hits[category] += 1
                signals.append(f"matched:{category}:{pattern}")

    category = "unknown"
    if category_hits:
        best_category, best_score = max(category_hits.items(), key=lambda item: (item[1], item[0]))
        if best_score > 0:
            category = best_category

    hard_hits = [pattern for pattern in HARD_PATTERNS if re.search(pattern, lowered)]
    complexity = "hard" if hard_hits else "easy"
    for pattern in hard_hits:
        signals.append(f"matched:hard:{pattern}")

    if category_hits.get("implementation", 0) > 0 and category_hits.get("debugging", 0) > 0:
        complexity = "hard"
        signals.append("derived:implementation+debugging")

    if requested_difficulty in {"easy", "hard"}:
        complexity = requested_difficulty
        signals.append(f"override:difficulty:{requested_difficulty}")

    return {
        "category": category,
        "complexity": complexity,
        "signals": signals,
    }


def validate_routing_config(config: dict[str, Any], *, target: str) -> dict[str, Any]:
    auto_payload = config.get("auto_routing")
    auto_routing = auto_payload if isinstance(auto_payload, dict) else {}
    enabled_agents = auto_routing.get("enabled_agents") or list(PRIMARY_AGENTS)
    optional_allowlist = auto_routing.get("optional_allowlist") or []

    if not isinstance(enabled_agents, list) or not enabled_agents:
        raise ValueError("Routing config auto_routing.enabled_agents must be a non-empty list.")
    normalized_enabled = [normalize_target(item) for item in enabled_agents]
    invalid_enabled = [item for item in normalized_enabled if item not in PRIMARY_AGENTS]
    if invalid_enabled:
        raise ValueError(
            "Routing config auto_routing.enabled_agents may only include the primary agents: "
            "codex, claude, cursor, opencode."
        )

    if not isinstance(optional_allowlist, list):
        raise ValueError("Routing config auto_routing.optional_allowlist must be a list when present.")
    normalized_optional = [normalize_target(item) for item in optional_allowlist]

    agents_payload = config.get("agents")
    agents = agents_payload if isinstance(agents_payload, dict) else {}

    if target == "auto":
        for agent in normalized_optional:
            definition = agents.get(agent)
            if not isinstance(definition, dict):
                raise ValueError(f"Optional auto-routing agent '{agent}' is missing an agents.{agent} config block.")
            scores = definition.get("scores")
            if not isinstance(scores, dict):
                raise ValueError(f"Optional auto-routing agent '{agent}' must define agents.{agent}.scores.")
            missing = [category for category in CATEGORIES if category != "unknown" and category not in scores]
            if missing:
                raise ValueError(
                    f"Optional auto-routing agent '{agent}' is missing scores for: {', '.join(sorted(missing))}."
                )

    return {
        "enabled_agents": normalized_enabled,
        "optional_allowlist": normalized_optional,
        "agents": agents,
    }


def score_agent(agent: str, category: str, complexity: str, validated_config: dict[str, Any]) -> int:
    if agent in DEFAULT_SCORES:
        score = int(DEFAULT_SCORES[agent].get(category, 0))
    else:
        score = int(validated_config["agents"][agent]["scores"].get(category, 0))

    if complexity == "easy" and agent == "opencode" and category == "simple_edit":
        score += 2
    if complexity == "hard" and agent == "cursor" and category in {"implementation", "debugging", "refactor"}:
        score += 2
    if category == "review" and agent == "codex":
        score += 4
    if category == "research" and agent in {"codex", "claude"}:
        score += 1
    return score


def route_task(
    *,
    task: str,
    target: str,
    requested_difficulty: str = "auto",
    cwd: str | None = None,
) -> dict[str, Any]:
    normalized_target = normalize_target(target)
    classifier = classify_task(task, requested_difficulty=requested_difficulty)

    if normalized_target != "auto":
        return {
            "route": [normalized_target],
            "difficulty": classifier["complexity"],
            "classifier": classifier,
            "route_reason": f"Explicit target '{normalized_target}' requested.",
            "scores": [],
            "routing_config": {
                "enabled_agents": list(PRIMARY_AGENTS),
                "optional_allowlist": [],
            },
        }

    config = load_routing_config(cwd)
    validated = validate_routing_config(config, target=normalized_target)
    pool = list(dict.fromkeys(validated["enabled_agents"] + validated["optional_allowlist"]))

    category = classifier["category"]
    effective_category = category if category != "unknown" else "implementation"
    scored = [
        {
            "agent": agent,
            "score": score_agent(agent, effective_category, classifier["complexity"], validated),
        }
        for agent in pool
    ]
    scored.sort(key=lambda item: (-item["score"], item["agent"]))

    limit = 1 if effective_category in {"research", "review"} else 2
    route = [item["agent"] for item in scored[:limit]]

    if effective_category == "implementation" and len(route) >= 2 and route[0] == "codex":
        first_score = scored[0]["score"]
        second_score = scored[1]["score"]
        if scored[1]["agent"] != "codex" and first_score - second_score <= 1:
            route = [scored[1]["agent"], "codex"]

    return {
        "route": route,
        "difficulty": classifier["complexity"],
        "classifier": classifier,
        "route_reason": (
            f"Auto-selected from primary pool {validated['enabled_agents']}"
            + (
                f" plus optional agents {validated['optional_allowlist']}"
                if validated["optional_allowlist"]
                else ""
            )
            + f" for category={classifier['category']} complexity={classifier['complexity']}."
        ),
        "scores": scored,
        "routing_config": {
            "enabled_agents": validated["enabled_agents"],
            "optional_allowlist": validated["optional_allowlist"],
        },
    }
