"""agent_gate.py 시험 — 실제 훅처럼 표준입력으로 JSON을 넣고 종료 코드를 본다(0 통과 · 2 막음)."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "subsub-agent"
HOOK = PLUGIN / "hooks" / "agent_gate.py"
SKILL = PLUGIN / "skills" / "agent-brief" / "SKILL.md"


@pytest.fixture(scope="session", autouse=True)
def _empty_home(tmp_path_factory):
    """시험하는 사람의 진짜 ~/.claude/agents 를 읽지 않게 빈 홈으로 돌린다"""
    global _HOME
    _HOME = str(tmp_path_factory.mktemp("home"))


_HOME = ""


def run(prompt, typ="general-purpose", model="opus", env=None, raw=None):
    ti = {"prompt": prompt, "subagent_type": typ}
    if model is not None:
        ti["model"] = model
    data = raw if raw is not None else json.dumps({"tool_input": ti}).encode("utf-8")
    base = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    base.update({"HOME": _HOME, "USERPROFILE": _HOME})
    p = subprocess.run([sys.executable, "-X", "utf8", str(HOOK)], input=data, capture_output=True, env={**base, **(env or {})})
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")


def code(*a, **k):
    return run(*a, **k)[0]


# 1. MODE 줄


def test_mode_missing_blocks():
    rc, _, err = run("count files")
    assert rc == 2 and "MODE" in err


@pytest.mark.parametrize("line", ["MODE: 독립", "MODE: 협동", "MODE: independent", "MODE:cooperative"])
def test_mode_present_passes(line):
    assert code(f"{line}\ncount files") == 0


def test_mode_placeholder_left_unfilled_blocks():
    """뼈대를 그대로 붙이고 칸을 안 채운 꼴은 판을 고른 게 아니다"""
    assert code("MODE: <독립 | 협동>\ncount files") == 2


# 2. model 칸


@pytest.mark.parametrize("model", [None, "", "inherit", " Inherit "])
def test_model_missing_or_inherit_blocks(model):
    rc, _, err = run("MODE: 협동\ncount files", model=model)
    assert rc == 2 and "model" in err


@pytest.mark.parametrize("typ", ["Explore", "explore", "Plan", "claude-code-guide", "statusline-setup", "fork"])
def test_skip_types_need_neither_mode_nor_model(typ):
    assert code("look around", typ, model=None) == 0


def test_definition_with_pinned_model_passes(tmp_path):
    a = tmp_path / ".claude" / "agents"
    a.mkdir(parents=True)
    (a / "pinned.md").write_text("---\nname: pinned\nmodel: opus\n---\nbody\n", encoding="utf-8")
    (a / "loose.md").write_text("---\nname: loose\n---\nbody\n", encoding="utf-8")
    (a / "follows.md").write_text("---\nname: follows\nmodel: inherit\n---\nbody\n", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    assert code("MODE: 독립\nreview", "pinned", model=None, env=env) == 0
    assert code("MODE: 독립\nreview", "loose", model=None, env=env) == 2
    assert code("MODE: 독립\nreview", "follows", model=None, env=env) == 2


def test_plugin_agent_found_by_name_after_colon():
    """플러그인 일꾼은 「플러그인:이름」으로 불린다 — 이 플러그인의 readonly-reviewer는 정의에 model이 박혀 있다"""
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    assert code("MODE: 독립\nreview the diff", "subsub-agent:readonly-reviewer", model=None, env=env) == 0


# 3. 작은 모델 + 뼈대 문서


@pytest.mark.parametrize("model", ["sonnet", "haiku", "claude-sonnet-5-5"])
def test_skeleton_doc_on_small_model_blocks(model):
    rc, _, err = run("MODE: 협동\nshorten CLAUDE.md section 2", model=model)
    assert rc == 2 and "CLAUDE.md" in err


def test_skeleton_doc_on_big_model_or_code_sign_passes():
    assert code("MODE: 협동\nshorten CLAUDE.md section 2", model="opus") == 0
    assert code("MODE: 협동\ngrep CLAUDE.md path in build.py\n모델 문지기: 코드 일", model="sonnet") == 0


def test_skip_types_still_get_skeleton_check_except_guide():
    for t in ("Explore", "Plan", "fork"):
        assert code("summarize CLAUDE.md rules", t, model="haiku") == 2, t
        assert code("summarize CLAUDE.md rules", t, model=None) == 0, t  # 모델을 모르면 안 본다
    assert code("how do hooks read CLAUDE.md?", "claude-code-guide", model="haiku") == 0


def test_brief_block_alone_does_not_trip_skeleton_check():
    """스킬 1절 블록을 그대로 넣은 작은 모델 일꾼은 통과 — 블록 안 「메모리 폴더에 쓰지 마」는 금지 줄일 뿐"""
    m = re.search(r"```\n(MODE:.*?)```", SKILL.read_text(encoding="utf-8"), re.S)
    assert m, "스킬 1절 블록을 못 찾음"
    block = m.group(1).replace("MODE: <독립 | 협동>", "MODE: 독립", 1)
    assert "<독립 | 협동>" not in block and "협동:" in block  # 첫 줄 뜻풀이(독립: … 협동: …)는 남아도 통과해야 한다
    # 쓸 때처럼 PROJECT RULES 자리표시 줄을 실제 규칙 한 줄로 바꾼다(자리표시 글에는 「CLAUDE.md」 낱말이 들어 있다)
    block = re.sub(r"(?m)^<프로젝트 금지 줄[^\n]*$", "- Never touch the data/raw folder.", block)
    assert "- Never touch the data/raw folder." in block, "자리표시 줄을 못 바꿈"
    for model in ("sonnet", "haiku"):
        assert code(block + "\ncount the csv rows", model=model) == 0, model
        assert code(block + "\nrewrite CLAUDE.md section 2", model=model) == 2, model


def test_project_extra_words(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agent-gate.json").write_text(json.dumps({"skeletonWords": ["현황\\.md"]}), encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    assert code("MODE: 협동\n현황.md 정리", model="sonnet", env=env) == 2
    assert code("MODE: 협동\n현황.md 정리", model="sonnet") == 0


def test_bad_extra_word_keeps_core_and_notices(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agent-gate.json").write_text(json.dumps({"skeletonWords": ["(", 3, "plan\\.md"]}), encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    assert code("MODE: 협동\nedit plan.md", model="sonnet", env=env) == 2  # 멀쩡한 낱말은 지킨다
    assert code("MODE: 협동\nedit CLAUDE.md", model="sonnet", env=env) == 2  # 공통 낱말도
    rc, out, _ = run("MODE: 협동\ncount rows", model="sonnet", env=env)
    assert rc == 0 and "agent-gate.json" in out  # 막지 않고 알린다


def test_broken_config_file_does_not_block(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agent-gate.json").write_text("{not json", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    rc, out, _ = run("MODE: 협동\ncount rows", model="sonnet", env=env)
    assert rc == 0 and "agent-gate.json" in out


# 감사 뒤 더한 것 — 고치기 전에 빨개야 한다


def test_mode_must_be_on_first_line():
    assert code("count files\nMODE: 독립") == 2
    assert code("Do not use MODE: 독립 here\ncount files") == 2
    assert code("\n  MODE: 독립\ncount files") == 0


def test_mode_both_left_in_blocks():
    """자리표시의 <>만 지우고 둘 다 남긴 꼴은 고른 게 아니다"""
    assert code("MODE: 독립 | 협동\ncount files") == 2


def test_web_fetch_is_skipped():
    assert code("https://example.com 요약", "web-fetch", model=None) == 0


def test_prohibition_lines_do_not_count_as_handling():
    """「CLAUDE.md는 고치지 마」 같은 금지 줄은 뼈대 문서를 다루는 일이 아니다"""
    pr = "MODE: 독립\nPROJECT RULES:\n- Never edit CLAUDE.md or files under .claude/\n- Don't open SKILL.md\ncount rows in data.csv"
    assert code(pr, model="sonnet") == 0
    assert code(pr + "\nthen shorten CLAUDE.md", model="sonnet") == 2


def test_code_folder_named_memory_is_not_claude_memory():
    assert code("MODE: 협동\nfix bug in src/memory/alloc.rs", model="sonnet") == 0
    assert code("MODE: 협동\ntidy ~/.claude/projects/foo/memory/notes.md", model="sonnet") == 2


def test_own_plugin_agent_wins_over_project_file_of_same_name(tmp_path):
    """「플러그인:이름」은 그 플러그인 판이 뜬다 — 같은 이름의 프로젝트 파일(model 없음)을 보면 안 된다"""
    a = tmp_path / ".claude" / "agents"
    a.mkdir(parents=True)
    (a / "readonly-reviewer.md").write_text("---\nname: readonly-reviewer\n---\nbody\n", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    assert code("MODE: 독립\nreview", "subsub-agent:readonly-reviewer", model=None, env=env) == 0
    assert code("MODE: 독립\nreview", "readonly-reviewer", model=None, env=env) == 2  # 이름만 부르면 프로젝트 파일이 뜬다


def test_plugin_agent_in_subfolder(tmp_path):
    root = tmp_path / "plug"
    (root / "agents" / "review").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "my-plugin"}', encoding="utf-8")
    (root / "agents" / "review" / "security.md").write_text("---\nname: security\nmodel: opus\n---\nb\n", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(root)}
    assert code("MODE: 독립\nreview", "my-plugin:review:security", model=None, env=env) == 0


def test_other_plugin_agent_model_not_blocked():
    """남의 플러그인 정의는 볼 수 없다 — model 검사로 막지 않는다(MODE 검사는 그대로)"""
    assert code("MODE: 독립\nreview", "otherplugin:pinned", model=None) == 0
    assert code("review", "otherplugin:pinned", model=None) == 2


def test_project_can_turn_gate_off(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agent-gate.json").write_text('{"enabled": false}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    assert code("count files", model=None, env=env) == 0


def test_hook_command_runs_under_bash():
    """hooks.json 명령을 실제 셸로 돌려 본다 — python3 / python 중 도는 쪽으로"""
    import shutil

    git_bash = pathlib.Path(os.environ.get("ProgramFiles", "C:/Program Files"), "Git", "bin", "bash.exe")
    bash = shutil.which("bash") or (str(git_bash) if git_bash.is_file() else None)
    if not bash:
        pytest.skip("bash 없음")
    cmd = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": PLUGIN.as_posix(), "PYTHONUTF8": "1"}
    env.pop("CLAUDE_PROJECT_DIR", None)
    for prompt, want in (("count files", 2), ("MODE: 독립\ncount files", 0)):
        data = json.dumps({"tool_input": {"prompt": prompt, "subagent_type": "general-purpose", "model": "opus"}}).encode()
        p = subprocess.run([bash, "-c", cmd], input=data, capture_output=True, env=env)
        assert p.returncode == want, (prompt, p.stderr.decode("utf-8", "replace"))


# 두 번째 감사 뒤 — 고치기 전에 빨개야 한다


def test_definition_found_in_subfolder_by_name_field(tmp_path):
    """Claude Code는 agents 폴더를 하위 폴더까지 훑고 머리의 name으로 일꾼을 찾는다 — 파일 이름이 아니다"""
    a = tmp_path / ".claude" / "agents"
    (a / "sub").mkdir(parents=True)
    (a / "sub" / "deep.md").write_text("---\nname: deep\nmodel: opus\n---\nb\n", encoding="utf-8")
    (a / "file-name.md").write_text("---\nname: real-name\nmodel: opus\n---\nb\n", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    assert code("MODE: 독립\nreview", "deep", model=None, env=env) == 0
    assert code("MODE: 독립\nreview", "real-name", model=None, env=env) == 0


@pytest.mark.parametrize(
    "line",
    ["- CLAUDE.md는 건드리지 마", "* Never edit CLAUDE.md", "Never edit CLAUDE.md", "- CLAUDE.md 고치지 말 것"],
)
def test_more_prohibition_forms_are_not_handling(line):
    assert code(f"MODE: 독립\n{line}\ncount rows in data.csv", model="sonnet") == 0


@pytest.mark.parametrize(
    "line",
    ["- Don't forget to update CLAUDE.md with the new rule", "- No need to ask, just rewrite CLAUDE.md"],
)
def test_lines_that_only_look_like_prohibitions_still_count(line):
    assert code(f"MODE: 협동\n{line}", model="sonnet") == 2


# 올리기 전 마지막 감사 뒤 — 고치기 전에 빨개야 한다


def _cfg(tmp_path, text):
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "agent-gate.json").write_text(text, encoding="utf-8")
    return {"CLAUDE_PROJECT_DIR": str(tmp_path)}


def test_inline_flag_word_does_not_switch_off_the_check(tmp_path):
    """낱말을 한 줄로 이어 붙이면 「(?i)…」가 든 낱말 하나가 검사 전체를 끈다 — 낱말마다 따로 본다"""
    env = _cfg(tmp_path, json.dumps({"skeletonWords": ["(?i)todo"]}))
    assert code("MODE: 협동\nrewrite CLAUDE.md", model="sonnet", env=env) == 2
    assert code("MODE: 협동\ntidy the TODO list", model="sonnet", env=env) == 2


def test_word_that_matches_nothing_is_dropped(tmp_path):
    env = _cfg(tmp_path, json.dumps({"skeletonWords": [".*", "x|"]}))
    rc, out, _ = run("MODE: 협동\ncount rows", model="sonnet", env=env)
    assert rc == 0 and "agent-gate.json" in out


@pytest.mark.parametrize(
    "line",
    [
        "Never edit tests; instead rewrite CLAUDE.md section 2",
        "Don't touch src/, just shorten CLAUDE.md",
        "Do not change the tone when you rewrite CLAUDE.md",
        "Never delete a rule while you shorten CLAUDE.md",
        "CLAUDE.md 2절을 줄여 줘, 다른 파일은 건드리지 마",
        "CLAUDE.md를 고치지 않으면 안 된다 — 2절을 줄여",
    ],
)
def test_task_on_the_same_line_as_a_prohibition_still_counts(line):
    assert code(f"MODE: 협동\n{line}", model="sonnet") == 2


@pytest.mark.parametrize(
    "line",
    [
        "- Never edit CLAUDE.md, SKILL.md or memory notes",
        "CLAUDE.md, SKILL.md는 건드리지 마",
        "- No edits to CLAUDE.md",
        "Don’t edit CLAUDE.md",
        "1. Never edit CLAUDE.md",
        "> Never read CLAUDE.md",
        "CLAUDE.md 줄이지 마",
    ],
)
def test_list_and_other_prohibition_forms_are_not_handling(line):
    assert code(f"MODE: 독립\n{line}\ncount rows in data.csv", model="sonnet") == 0


@pytest.mark.parametrize("first", ["MODE: 독립 and 협동", "MODE: 협동 (or 독립 if unsure)"])
def test_mode_both_more_forms(first):
    assert code(f"{first}\ncount files") == 2


@pytest.mark.parametrize(
    "first",
    ["**MODE: 독립**", "# MODE: 독립", "﻿MODE: 독립", "MODE: 독립 — 독립: you judge alone. 협동: you build one piece."],
)
def test_mode_line_with_markup_or_gloss_passes(first):
    assert code(f"{first}\ncount files") == 0


def test_bad_config_is_reported_whatever_the_model(tmp_path):
    env = _cfg(tmp_path, '{"enabled": "false"}')
    rc, out, _ = run("MODE: 협동\ncount rows", model="opus", env=env)
    assert rc == 0 and "enabled" in out  # 꺼지지 않았다고 알린다
    rc, _, err = run("count rows", model="opus", env=env)
    assert rc == 2 and "enabled" in err  # 막을 때도 까닭 옆에
    env = _cfg(tmp_path, "{not json")
    rc, out, _ = run("MODE: 협동\ncount rows", model="opus", env=env)
    assert rc == 0 and "agent-gate.json" in out


def test_long_repetitive_input_is_fast():
    import time

    t0 = time.time()
    assert code("MODE: 협동\n" + ".claude/projects/" * 12000, model="sonnet") == 0
    assert time.time() - t0 < 5


def test_dot_dot_in_agent_name_is_not_a_path():
    assert code("MODE: 독립\nreview", "subsub-agent:..:..:x", model=None, env={"CLAUDE_PLUGIN_ROOT": str(PLUGIN)}) == 2


# 입력이 이상하면 막지 않는다


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'{"tool_input": "x"}'])
def test_bad_input_passes(raw):
    assert code("", raw=raw) == 0


# 묶음 모양


def test_hooks_json_points_to_script():
    h = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    entry = h["hooks"]["PreToolUse"][0]
    assert re.search(entry["matcher"], "Agent") and re.search(entry["matcher"], "Task")
    assert "agent_gate.py" in entry["hooks"][0]["command"]


def test_reviewer_has_no_edit_tools_and_pins_model():
    text = (PLUGIN / "agents" / "readonly-reviewer.md").read_text(encoding="utf-8")
    head = re.match(r"---\n(.*?)\n---", text, re.S).group(1)
    assert re.search(r"^model:\s*opus\s*$", head, re.M)
    tools = re.search(r"^disallowedTools:\s*(.+)$", head, re.M).group(1)
    for t in ("Edit", "Write", "NotebookEdit"):
        assert t in tools


def test_readme_images_exist():
    """README가 가리키는 그림 파일이 저장소에 다 있나(깃헙에서 깨진 그림으로 보이지 않게)"""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    srcs = re.findall(r'<img src="([^"]+)"', readme)
    assert srcs, "README에 그림이 없다"
    missing = [s for s in srcs if not (ROOT / s).is_file()]
    assert not missing, missing


def test_no_private_words():
    """올리기 전 점검 — 저장소 밖 낱말 목록(한 줄에 정규식 하나)으로 개인 흔적을 찾는다. 목록은 저장소에 넣지 않는다.
    자리: 환경 변수 SUBSUB_AGENT_PRIVATE_WORDS, 없으면 저장소 옆 subsub-agent.private-words.txt"""
    src = os.environ.get("SUBSUB_AGENT_PRIVATE_WORDS") or str(ROOT.parent / "subsub-agent.private-words.txt")
    p = pathlib.Path(src)
    if not p.is_file():
        pytest.skip("개인 낱말 목록 없음")
    words = [w for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
    bad = re.compile("|".join(f"(?:{w})" for w in words), re.I)
    hits = []
    for f in ROOT.rglob("*"):
        if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert not hits, "\n".join(hits)
