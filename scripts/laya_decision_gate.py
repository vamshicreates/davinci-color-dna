#!/usr/bin/env python3
"""
Embedded Laya Non-Autoregressive Decision Gate (https://github.com/NandhaKishorM/laya)
for DaVinci Resolve (`davinci-color-dna`).

CORE POLICY — CALL LAYA ONLY WHEN NECESSARY:
1. Basic / Explicit / Direct Tasks -> BYPASS Laya completely (`laya_called: False`) and execute
   manually using the skill's native CLI/MCP tools with zero model overhead.
2. Complex / Ambiguous Multi-Branch Creative Routing -> Invoke Laya's `Router` in a single
   non-autoregressive forward pass (`choice`, `score`, `noul`) to make structured decisions.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Tuple

SKILL_NAME = "davinci-color-dna"
APP_LABEL = "DaVinci Resolve"
DOMAIN = "davinci"
LAYA_REPO_URL = "https://github.com/NandhaKishorM/laya"

DEFAULT_PRESET_QUESTIONS: Dict[str, Any] = {
    "grading_pipeline_mode": {
        "type": "choice",
        "instructions": "Which DaVinci Resolve grading pipeline mode best suits this reference match?",
        "criteria": {
            "hybrid_cdl_plus_split_lut": "combine Node 1 ASC-CDL primary balance with Node 2 33x33x33 Split-Tone 3D .cube LUT",
            "primary_cdl_clean": "use pure ASC-CDL Slope/Offset/Power/Saturation for clean, natural exposure and white-balance matching",
            "full_emulation_3d_lut": "bake full filmic S-curve, toe/shoulder roll-off, and chromaticity transfer into the 3D .cube LUT"
        }
    },
    "look_aggressiveness": {
        "type": "score",
        "instructions": "How aggressively should the reference image's color cast override the source clip?",
        "criteria": [
            "subtle_balanced_match",
            "faithful_cinematic_transfer",
            "stylized_full_genre_grade"
        ]
    },
    "protect_skin_tones": {
        "type": "noul",
        "instructions": "Should midtone gamma and saturation swings be dampened to protect human skin tones?",
        "criteria": {
            "true": "the shot features people, talking heads, portraits, or actors where natural skin is critical",
            "false": "the shot is b-roll, product, landscape, architecture, or stylized sci-fi without natural skin constraints"
        },
        "labels": {
            "true": "A",
            "false": "B"
        }
    }
}

BASIC_TASK_PATTERNS = [
    r"^\s*(status|ping|list|check|inspect|export|save|open|close|render|screenshot)\b",
    r"\b(set|change|move|rotate|scale|resize|delete|remove|copy|add marker|cut silence)\b.*\b(to|by|at|from)\b.*(\d|#[0-9a-fA-F]{3,6}|true|false)",
]

COMPLEX_DECISION_INDICATORS = [
    "decide", "choose", "which style", "which layout", "best approach", "ambiguous",
    "campaign", "multi-", "full brand", "creative direction", "triage", "archetype",
    "choreography", "trade-off", "compare", "automat", "from scratch", "redesign",
]


def should_call_laya(state_text: str, force: bool = False) -> Tuple[bool, str]:
    """
    Deterministic complexity gate that decides whether Laya is genuinely needed.
    Returns (call_laya: bool, reason: str).
    """
    if force:
        return True, "Forced via --force-laya flag."

    clean = (state_text or "").strip()
    if not clean:
        return False, "Empty state — basic manual execution."

    lower = clean.lower()
    word_count = len(clean.split())

    if word_count <= 12 and not any(k in lower for k in COMPLEX_DECISION_INDICATORS):
        return (
            False,
            f"Basic/direct task detected ({word_count} words, no multi-branch ambiguity). "
            "Bypassing Laya and executing directly.",
        )

    for pat in BASIC_TASK_PATTERNS:
        if re.search(pat, lower) and not any(k in lower for k in COMPLEX_DECISION_INDICATORS):
            return (
                False,
                "Explicit parameter/command pattern detected. Bypassing Laya and executing directly.",
            )

    matched_indicators = [k for k in COMPLEX_DECISION_INDICATORS if k in lower]
    if matched_indicators or word_count >= 22:
        return (
            True,
            f"Complex/multi-branch creative decision detected (words={word_count}, "
            f"indicators={matched_indicators[:3]}). Calling Laya Router.",
        )

    return (
        False,
        "Standard task without multi-branch ambiguity. Bypassing Laya and executing directly.",
    )


def sanitize_questions_for_laya(questions: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforce NandhaKishorM/laya best practices:
      - `noul` questions must have explicit `criteria` keyed by 'true'/'false'
        and neutral `labels: {'true': 'A', 'false': 'B'}` (Issue #156).
    """
    sanitized = {}
    for q_key, q_spec in questions.items():
        spec = dict(q_spec)
        if spec.get("type") == "noul":
            if "criteria" not in spec or not isinstance(spec["criteria"], dict):
                instr = spec.get("instructions", "Does the statement hold?")
                spec["criteria"] = {
                    "true": f"yes, {instr}",
                    "false": f"no, not ({instr})",
                }
            if "labels" not in spec:
                spec["labels"] = {"true": "A", "false": "B"}
        sanitized[q_key] = spec
    return sanitized


def heuristic_fallback_answers(state_text: str, questions: Dict[str, Any]) -> Dict[str, Any]:
    """Fast zero-overhead deterministic fallback when Laya is bypassed or not yet installed."""
    lower = (state_text or "").lower()
    answers = {}
    for q_key, q_spec in questions.items():
        q_type = q_spec.get("type", "choice")
        criteria = q_spec.get("criteria", {})
        if q_type == "choice" and isinstance(criteria, dict) and criteria:
            best_opt = next(iter(criteria.keys()))
            best_hits = -1
            for opt_k, opt_desc in criteria.items():
                words = [w for w in re.findall(r"[a-z0-9]+", f"{opt_k} {opt_desc}".lower()) if len(w) > 3]
                hits = sum(1 for w in words if w in lower)
                if hits > best_hits:
                    best_hits = hits
                    best_opt = opt_k
            answers[q_key] = {"choice": best_opt, "confidence": 0.78, "source": "direct_heuristic"}
        elif q_type == "score" and isinstance(criteria, list) and criteria:
            mid_idx = min(len(criteria) - 1, max(0, len(criteria) // 2))
            answers[q_key] = {"score": criteria[mid_idx], "level_index": mid_idx, "confidence": 0.75, "source": "direct_heuristic"}
        elif q_type == "noul":
            answers[q_key] = {"noul": 0.65, "bool": True, "confidence": 0.75, "source": "direct_heuristic"}
    return answers


def evaluate_decision(
    state_text: str,
    questions: Dict[str, Any] = None,
    force_laya: bool = False,
    min_confidence: float = 0.55,
) -> Dict[str, Any]:
    """
    Evaluate typed decisions with strict complexity gating:
      - If `should_call_laya` is False -> returns immediately (`laya_called: False`, `execution_mode: "direct_manual_execution"`).
      - If `should_call_laya` is True  -> calls `laya.Router().predict(...)` (`laya_called: True`).
    """
    active_questions = sanitize_questions_for_laya(questions or DEFAULT_PRESET_QUESTIONS)
    call_needed, gate_reason = should_call_laya(state_text, force=force_laya)

    if not call_needed:
        return {
            "skill": SKILL_NAME,
            "domain": DOMAIN,
            "laya_called": False,
            "execution_mode": "direct_manual_execution",
            "gate_reason": gate_reason,
            "instruction": "Task is basic/explicit. Do NOT load Laya; execute the action directly using native skill tools.",
            "recommended_defaults": heuristic_fallback_answers(state_text, active_questions),
        }

    try:
        from laya import Router  # type: ignore

        router = Router()
        raw_result = router.predict(state_text, active_questions, min_confidence=min_confidence)
        return {
            "skill": SKILL_NAME,
            "domain": DOMAIN,
            "laya_called": True,
            "execution_mode": "laya_system1_router",
            "gate_reason": gate_reason,
            "routing": raw_result.get("routing", {"model": "english"}),
            "answers": raw_result.get("answers", {}),
        }
    except ImportError:
        return {
            "skill": SKILL_NAME,
            "domain": DOMAIN,
            "laya_called": False,
            "laya_installed": False,
            "execution_mode": "fallback_direct_execution",
            "gate_reason": gate_reason,
            "install_hint": "Run `python3 scripts/laya_decision_gate.py --install` or `pip install laya` to enable neural Laya routing.",
            "answers": heuristic_fallback_answers(state_text, active_questions),
        }
    except Exception as exc:
        return {
            "skill": SKILL_NAME,
            "domain": DOMAIN,
            "laya_called": False,
            "execution_mode": "fallback_after_error",
            "gate_reason": gate_reason,
            "laya_error": str(exc),
            "answers": heuristic_fallback_answers(state_text, active_questions),
        }


def install_laya_package() -> Dict[str, Any]:
    """Install NandhaKishorM/laya via pip (`pip install laya` with GitHub fallback)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "laya"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", f"git+{LAYA_REPO_URL}.git"],
                capture_output=True,
                text=True,
                timeout=300,
            )
        return {
            "status": "installed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-600:],
            "stderr": proc.stderr[-600:],
        }
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Laya Decision Gate for {APP_LABEL} ({LAYA_REPO_URL}) — Calls Laya ONLY when necessary."
    )
    parser.add_argument("--state", default="", help="User brief, task description, or state text to evaluate")
    parser.add_argument("--questions-json", default="", help="Optional custom questions JSON string or file path")
    parser.add_argument("--force-laya", action="store_true", help="Force calling Laya even for short/basic states")
    parser.add_argument("--min-confidence", type=float, default=0.55, help="Minimum confidence threshold (default: 0.55)")
    parser.add_argument("--install", action="store_true", help="Install the `laya` package (https://github.com/NandhaKishorM/laya)")
    args = parser.parse_args()

    if args.install:
        res = install_laya_package()
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") == "installed" else 1

    custom_q = None
    if args.questions_json:
        p = Path(args.questions_json)
        if p.exists():
            custom_q = json.loads(p.read_text(encoding="utf-8"))
        else:
            custom_q = json.loads(args.questions_json)

    res = evaluate_decision(
        state_text=args.state,
        questions=custom_q,
        force_laya=args.force_laya,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
