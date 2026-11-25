"""agent_gate — 서브에이전트를 띄우기 직전(PreToolUse: Agent)에 지시문을 보고, 빠진 것이 있으면 막는다(종료 코드 2).

막는 것 셋:
  1. 첫 줄에 MODE가 없다 — 지시문 첫 줄이 「MODE: 독립」 또는 「MODE: 협동」(independent / cooperative)이어야 한다.
     감사 · 검증 일꾼이 메인의 결론을 듣거나 다른 일꾼과 말을 맞추면, 독립 감사가 아니라 메아리가 된다.
     띄울 때마다 고르게 하고 기계로 지킨다.
  2. model 칸이 없다 — 안 적으면 일꾼이 메인 세션의 모델(제일 비싼 것일 수 있다)을 그대로 물려받는다.
     에이전트 정의 파일 머리에 model이 박힌 일꾼은 통과. 「inherit」은 안 적은 것으로 본다(결과가 같으니까).
     남의 플러그인 일꾼(「다른플러그인:이름」)은 정의 파일을 볼 수 없어 이 검사를 하지 않는다.
  3. 작은 모델(sonnet · haiku)에 뼈대 문서(CLAUDE.md · SKILL.md · Claude 메모리)를 맡긴다 — 규칙 글을 줄이다가 조건을
     날리는 일이 있었다. 「Never edit …」 · 「… 건드리지 마」 같은 금지하는 마디는 다루는 일이 아니라 빼고 본다
     (strip_prohibitions — 같은 줄에 일거리가 이어지면 그 뒤는 그대로 본다).
     문서 이름만 나오는 코드 일이면 지시문에 「모델 문지기: 코드 일」을 적으면 통과.

건너뜀: 조사만 하는 내장 일꾼(Explore · Plan · claude-code-guide · statusline-setup · web-fetch)과 fork는 1 · 2를 건너뛴다.
  3은 claude-code-guide만 빼고 그대로 본다(그 일꾼은 늘 작은 모델이라 질문에 「CLAUDE.md」만 나와도 막히므로).

프로젝트 설정: <프로젝트>/.claude/agent-gate.json
  "enabled": false      — 그 프로젝트에서 끈다(따옴표 없는 false만)
  "skeletonWords": [..] — 작은 모델에 맡기면 안 되는 문서 낱말(정규식)을 더한다. 낱말마다 따로 본다.
  못 읽는 파일 · 못 쓰는 값은 그것만 빼고 한 줄 알린다(막지 않음 · 막을 땐 까닭 옆에 같이).

입력을 못 읽거나 훅이 고장 나면 막지 않는다 — 문지기 고장으로 일이 멈추는 것보다 통과가 낫다.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

SKIP = {"explore", "claude-code-guide", "plan", "statusline-setup", "fork", "web-fetch"}
SKELETON_SKIP = {"claude-code-guide"}
# model을 안 적어도 늘 이 모델로 도는 내장 일꾼
BUILTIN_MODEL = {"claude-code-guide": "haiku", "web-fetch": "haiku"}

_MODES = r"협동|독립|independent|cooperative"
# 첫 줄 앞의 꾸밈(BOM · 「#」 · 「**」 · 「>」)은 봐준다
MODE_RE = re.compile(rf"\A[\s﻿#*>]*MODE:\s*({_MODES})\b", re.I)
_MODE_WORD = re.compile(_MODES, re.I)
SMALL_MODEL_RE = re.compile(r"sonnet|haiku", re.I)
CODE_SIGN = "모델 문지기: 코드 일"
SKELETON_CORE = (
    r"CLAUDE\.md|SKILL\.md|MEMORY\.md|auto-memory|메모리 폴더|자동 메모리"
    r"|\.claude[\\/]projects[\\/][^\s]{0,300}?[\\/]memory\b"
)
CONFIG_NAME = "agent-gate.json"

# 금지하는 마디 — 「이 문서를 건드리지 마」는 그 문서를 다루는 일이 아니다.
# 영어: 줄 머리(글머리 기호 · 번호 · 「>」 뒤)의 「Never · Do not · Don't + 동사」 또는 「No edits to」에서, 일거리로 넘어가는 말 앞까지.
_EN_START = re.compile(
    r"^[ \t]*(?:(?:[-*•>]|\d+[.)])[ \t]*)*"
    r"(?:(?:Never|Do not|Don['’]t)[ \t]+(?:ever[ \t]+)?(?:edit|touch|modify|open|read|change|write|delete|rewrite|update|move|rename)\b"
    r"|No[ \t]+(?:edits?|changes?|writes?|modifications?)[ \t]+(?:to|in|on)\b)",
    re.I,
)
_EN_CUT = re.compile(r";|—|–|\.\s|\b(?:instead|but|just|while|when|then|after|before)\b", re.I)
# 한국어: 「… 건드리지 마 / 고치지 말 것 / 수정하지 않는다」로 끝나는 마디만. 「고치지 않으면 안 된다」는 금지가 아니다.
_KO_SPLIT = re.compile(r"[;—–]|\.\s|(?<=[줘해라요다고서며면])\s*,")
_KO_END = re.compile(
    r"(?:건드리지|고치지|수정하지|열지|읽지|바꾸지|지우지|쓰지|옮기지|줄이지)[ \t]*"
    r"(?:마(?:라|세요|십시오)?|말[ \t]*것|말기|말아(?:[ \t]*줘)?|않는다|않기|않음)[ \t.!)]*$"
)


def strip_prohibitions(text: str) -> str:
    """금지하는 마디만 지운 글 — 같은 줄에 이어진 일거리는 남긴다."""
    out = []
    for line in text.split("\n"):
        m = _EN_START.match(line)
        if m:
            cut = _EN_CUT.search(line, m.end())
            line = line[cut.start() :] if cut else ""
        if _KO_END.search(line) or _KO_SPLIT.search(line):
            parts, last = [], 0
            for s in _KO_SPLIT.finditer(line):
                parts.append(line[last : s.start()])
                last = s.end()
            parts.append(line[last:])
            line = " ; ".join(p for p in parts if not _KO_END.search(p.strip()))
        out.append(line)
    return "\n".join(out)


def _strip_inherit(model: object) -> str:
    m = str(model or "").strip().strip("\"'").lower()
    return "" if m == "inherit" else m


def _head(f: pathlib.Path) -> str:
    text = f.read_bytes().decode("utf-8-sig", "replace")
    head = re.match(r"---\s*\r?\n(.*?)\r?\n---", text, re.S)
    return head.group(1) if head else ""


def _field(head: str, key: str) -> str:
    m = re.search(rf"^{key}:[ \t]*(\S+)", head, re.M)
    return m.group(1).strip("\"'") if m else ""


def _read_model(f: pathlib.Path) -> str:
    return _strip_inherit(_field(_head(f), "model"))


def _find_definition(root: pathlib.Path, name: str) -> pathlib.Path | None:
    """agents 폴더를 하위 폴더까지 훑어 머리의 name이 맞는 정의를 찾는다. name이 없는 파일은 파일 이름으로 맞춘다."""
    if not root.is_dir():
        return None
    by_stem = None
    for f in sorted(root.rglob("*.md")):
        try:
            declared = _field(_head(f), "name")
        except OSError:
            continue
        if declared == name:
            return f
        if not declared and f.stem == name and by_stem is None:
            by_stem = f
    return by_stem


def _own_plugin_name() -> str:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return ""
    try:
        return str(json.loads((pathlib.Path(root) / ".claude-plugin" / "plugin.json").read_bytes()).get("name") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def definition_model(agent_type: str) -> str | None:
    """정의 파일 머리의 model 칸 · 내장 고정 모델 · 모르면 ''. 남의 플러그인 일꾼이라 볼 수 없으면 None.

    「이름」 — 프로젝트(.claude/agents) → 사용자(~/.claude/agents) 순, Claude Code가 고르는 순서와 같다.
      두 폴더 다 하위 폴더까지 훑고, 머리의 name으로 맞춘다(Claude Code도 파일 이름이 아니라 name으로 찾는다).
    「플러그인:이름」 · 「플러그인:하위폴더:이름」 — 그 플러그인 판이 뜬다. 이 플러그인이면 그 폴더에서, 아니면 None.
    명령줄 --agents · 관리 설정으로 정한 일꾼은 볼 수 없어 ''(모름)으로 본다.
    """
    parts = (agent_type or "").split(":")
    # 조각은 글자 · 숫자 · 「-」 · 「_」로 시작해야 한다(「..」 같은 경로 조각은 받지 않는다)
    if not all(re.fullmatch(r"[\w-][\w.-]*", p) for p in parts):
        return ""
    if len(parts) > 1:
        if parts[0] != _own_plugin_name():
            return None
        agents = pathlib.Path(os.environ["CLAUDE_PLUGIN_ROOT"], "agents")
        exact = agents.joinpath(*parts[1:-1], f"{parts[-1]}.md")
        f = exact if exact.is_file() else _find_definition(agents, parts[-1])
        return _read_model(f) if f else ""
    dirs = []
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        dirs.append(pathlib.Path(os.environ["CLAUDE_PROJECT_DIR"]) / ".claude" / "agents")
    dirs.append(pathlib.Path.home() / ".claude" / "agents")
    for d in dirs:
        f = _find_definition(d, parts[0])
        if f:
            return _read_model(f)
    return BUILTIN_MODEL.get(parts[0].lower(), "")


def _config() -> tuple[dict, list[str]]:
    """(설정, 못 쓰는 것 목록)"""
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    cfg = pathlib.Path(root) / ".claude" / CONFIG_NAME if root else None
    if not cfg or not cfg.is_file():
        return {}, []
    try:
        data = json.loads(cfg.read_bytes().decode("utf-8-sig"))
    except ValueError as e:
        return {}, [f"파일을 못 읽음({type(e).__name__})"]
    if not isinstance(data, dict):
        return {}, ["객체가 아님"]
    bad = []
    if "enabled" in data and not isinstance(data["enabled"], bool):
        bad.append(f"enabled는 따옴표 없는 true · false만({data['enabled']!r}는 무시 — 훅은 켜져 있음)")
    return data, bad


def _skeleton_patterns(cfg: dict, bad: list[str]) -> list[re.Pattern[str]]:
    """공통 낱말 + 프로젝트 덧낱말. 낱말마다 따로 엮는다 — 이어 붙이면 「(?i)…」 하나가 전체를 깨뜨린다."""
    words = cfg.get("skeletonWords")
    if isinstance(words, str):
        words = [words]
    if words is not None and not isinstance(words, list):
        bad.append(f"skeletonWords가 목록이 아님({type(words).__name__})")
        words = []
    pats = [re.compile(SKELETON_CORE, re.I)]
    for w in words or []:
        if not isinstance(w, str) or not w:
            bad.append(f"skeletonWords {w!r:.30}")
            continue
        try:
            p = re.compile(w, re.I)
        except re.error:
            bad.append(f"skeletonWords {w[:30]}(정규식 아님)")
            continue
        if p.search(""):
            bad.append(f"skeletonWords {w[:30]}(빈 글에도 맞음)")
            continue
        pats.append(p)
    return pats


def _mode_problem(prompt: str) -> bool:
    if not MODE_RE.search(prompt):
        return True
    first = prompt.lstrip("﻿ \t\r\n").split("\n", 1)[0]
    head = re.split(r"—|–| - ", first, maxsplit=1)[0]  # 뒤따르는 뜻풀이는 보지 않는다
    kinds = {"i" if w.lower() in ("독립", "independent") else "c" for w in _MODE_WORD.findall(head)}
    return len(kinds) > 1  # 「독립 | 협동」처럼 고르지 않고 둘 다 남긴 꼴


def judge(tool_input: dict) -> tuple[str | None, str]:
    """(막을 까닭 | None, 막지 않는 알림)"""
    cfg, bad = _config()
    if cfg.get("enabled") is False:
        return None, ""
    block = _judge(tool_input, cfg, bad)
    note = f"{CONFIG_NAME} 설정 알림: {' · '.join(bad[:3])}" if bad else ""
    if block:
        return block + (f"  ({note})\n" if note else ""), ""
    return None, note


def _judge(tool_input: dict, cfg: dict, bad: list[str]) -> str | None:
    agent_type = str(tool_input.get("subagent_type") or "")
    kind = agent_type.lower()
    if kind in SKELETON_SKIP:
        return None
    skip = kind in SKIP
    prompt = str(tool_input.get("prompt") or "")

    if not skip and _mode_problem(prompt):
        return (
            "⛔ agent_gate: 지시문 첫 줄을 「MODE: 독립」 또는 「MODE: 협동」 하나로 하여 다시 띄운다.\n"
            "  독립 — 판정 · 검증하는 일, 또는 메인이 이미 결론을 가진 일 → 일꾼끼리 메시지 금지 · 메인은 사실만 답함\n"
            "  협동 — 서로 다른 조각을 나눠 만드는 일 → 지시문이 이름 댄 일꾼끼리 말해도 됨\n"
            "  섞이면 갈래를 쪼개고, 헷갈리면 독립.\n"
        )

    model = _strip_inherit(tool_input.get("model"))
    defined = definition_model(agent_type)
    if not skip and not model and defined == "":
        return (
            "⛔ agent_gate: model 칸을 적어 다시 띄운다 — 안 적으면 메인 세션의 모델을 그대로 물려받는다.\n"
            "  틀리면 아픈 일 · 규칙 문서는 큰 모델, 기계적인 세기 · 훑기는 작은 모델. 정의 파일에 model이 박힌 일꾼은 통과.\n"
        )

    running = model or defined or ""
    text = f"{tool_input.get('description') or ''}\n{prompt}"
    if SMALL_MODEL_RE.search(running) and CODE_SIGN not in text:
        body = strip_prohibitions(text)
        for pat in _skeleton_patterns(cfg, bad):
            hit = pat.search(body)
            if hit:
                return (
                    f"⛔ agent_gate: 뼈대 문서 「{hit.group(0)[:60]}」를 다루는 일을 {running}(으)로 띄우려 했다 — 큰 모델로 다시.\n"
                    f"  문서 이름만 나오고 문서를 다루지 않는 코드 일이면 지시문에 「{CODE_SIGN}」 한 줄.\n"
                )
    return None


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        tool_input = data.get("tool_input") if isinstance(data, dict) else None
        if not isinstance(tool_input, dict):
            return 0
        block, note = judge(tool_input)
    except Exception:  # noqa: BLE001 — 문지기가 고장 나면 막지 않는다
        return 0
    if block:
        sys.stderr.buffer.write(block.encode("utf-8"))
        return 2
    if note:
        msg = f"agent_gate ⚠ {note}"
        out = {"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
