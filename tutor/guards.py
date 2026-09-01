"""Deterministic output filter.

This is the only guardrail that does not depend on the model cooperating.
Everything else -- system prompt, few-shot, classifier -- is a model being
asked nicely. This layer parses what actually came out and refuses to pass
it on, no matter how the student talked the model into producing it.

Design principle: the rule is NOT "no code". A student stuck on ArrayList
syntax is not cheating, and refusing that just sends them to ChatGPT. The
rule is "no assignment-shaped solutions" -- which in practice means we
block on STRUCTURE (control flow, function bodies, length), not on the mere
presence of code characters.

KNOWN GAP: this cannot see a solution written as prose ("loop through the
array, track the max, compare each element..."). That is a real evasion and
it is the intent classifier's job, not this file's. Do not mistake a clean
pass here for a safe response.
"""
import ast
import re
import textwrap
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Policy:
    """Per-course thresholds. Policy is DATA, not code -- every course/school
    gets its own row, so a teacher can loosen or tighten without a deploy."""
    max_snippet_lines: int = 3
    block_control_flow: bool = True
    block_function_defs: bool = True
    allow_languages: tuple = ()      # e.g. ("sql",) to exempt a language


DEFAULT_POLICY = Policy()

FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
INLINE_RE = re.compile(r"`([^`\n]{1,120})`")

# C-family / Java structural signals.
C_CONTROL_RE = re.compile(r"\b(for|while|if|switch|do|try|catch|foreach)\s*\(")
C_FUNCDEF_RE = re.compile(
    r"\b(public|private|protected|static|final|void|int|double|float|long|"
    r"char|boolean|String|var)\b[^;=\n]*\([^)]*\)\s*\{"
)
PY_CONTROL_RE = re.compile(r"^\s*(for\s+\w+\s+in\s|while\s|if\s|elif\s|def\s+\w+\s*\(|class\s+\w+)", re.M)

CODEY_RE = re.compile(r"[;{}]|\b(return|int|void|public|def|class|let|const|var)\b|[=<>!+\-*/%]=|->|=>")


@dataclass
class Verdict:
    action: str = "allow"                     # "allow" | "block"
    reasons: list = field(default_factory=list)
    spans: list = field(default_factory=list)  # (start, end) offsets to redact

    @property
    def blocked(self) -> bool:
        return self.action == "block"


def _logical_lines(src: str) -> int:
    n = 0
    for ln in src.splitlines():
        s = ln.strip()
        if s and not s.startswith(("#", "//", "/*", "*")):
            n += 1
    return n


def _python_structure(src: str):
    """Ground truth for Python: if it parses, inspect the real AST."""
    for candidate in (src, textwrap.dedent(src)):
        try:
            tree = ast.parse(candidate)
            break
        except SyntaxError:
            continue
    else:
        return None  # not parseable Python -> caller falls back to heuristics
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hits.append(f"defines {type(node).__name__} '{node.name}'")
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            hits.append("contains a loop")
        elif isinstance(node, ast.If):
            hits.append("contains branching logic")
        elif isinstance(node, (ast.Try, ast.With)):
            hits.append("contains control flow")
    return hits


def _classify(src: str, lang: str, policy: Policy):
    """Return list of reasons this span is a solution, or [] if it's a snippet."""
    if lang.lower() in policy.allow_languages:
        return []

    reasons = []
    nlines = _logical_lines(src)

    py = _python_structure(src)
    if py is not None:
        for h in py:
            if "loop" in h or "branching" in h or "control flow" in h:
                if policy.block_control_flow:
                    reasons.append(f"python: {h}")
            else:
                if policy.block_function_defs:
                    reasons.append(f"python: {h}")
    else:
        if policy.block_control_flow and C_CONTROL_RE.search(src):
            reasons.append("c-family: contains control flow")
        if policy.block_control_flow and PY_CONTROL_RE.search(src):
            reasons.append("python-like: contains control flow")
        if policy.block_function_defs and C_FUNCDEF_RE.search(src):
            reasons.append("c-family: defines a method")

    if nlines > policy.max_snippet_lines:
        reasons.append(f"{nlines} logical lines (limit {policy.max_snippet_lines})")

    return sorted(set(reasons))


def scan(text: str, policy: Policy = DEFAULT_POLICY) -> Verdict:
    v = Verdict()

    for m in FENCE_RE.finditer(text):
        lang, body = m.group(1), m.group(2)
        why = _classify(body, lang, policy)
        if why:
            v.action = "block"
            v.reasons += [f"fenced block: {w}" for w in why]
            v.spans.append((m.start(), m.end()))

    # Unfenced code: runs of consecutive indented / syntax-dense lines.
    fenced = [(m.start(), m.end()) for m in FENCE_RE.finditer(text)]
    def in_fence(o):
        return any(a <= o < b for a, b in fenced)

    lines, run, start_off, off = text.splitlines(keepends=True), [], 0, 0
    for ln in lines:
        if in_fence(off):
            if len(run) >= 2:
                body = "".join(run)
                why = _classify(body, "", policy)
                if why:
                    v.action = "block"
                    v.reasons += [f"unfenced code: {w}" for w in why]
                    v.spans.append((start_off, start_off + len(body)))
            run = []
            off += len(ln)
            continue
        dense = bool(CODEY_RE.search(ln)) and len(ln.strip()) > 0
        indented = ln.startswith(("    ", "\t"))
        if dense or (indented and ln.strip()):
            if not run:
                start_off = off
            run.append(ln)
        else:
            if len(run) >= 2:
                body = "".join(run)
                why = _classify(body, "", policy)
                if why:
                    v.action = "block"
                    v.reasons += [f"unfenced code: {w}" for w in why]
                    v.spans.append((start_off, start_off + len(body)))
            run = []
        off += len(ln)
    if len(run) >= 2:
        body = "".join(run)
        why = _classify(body, "", policy)
        if why:
            v.action = "block"
            v.reasons += [f"unfenced code: {w}" for w in why]
            v.spans.append((start_off, start_off + len(body)))

    v.reasons = sorted(set(v.reasons))
    return v


def redact(text: str, verdict: Verdict, note: str = "[solution code withheld]") -> str:
    out, last = [], 0
    for s, e in sorted(verdict.spans):
        if s < last:
            continue
        out.append(text[last:s]); out.append(note); last = e
    out.append(text[last:])
    return "".join(out)
