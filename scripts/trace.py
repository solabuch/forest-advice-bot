"""Трасування «вимога → код → тест»: таблиця з requirements.md, тестів і eval-кейсів.

Таблицю не ведемо вручну: ID вимог (R-17, D-43) стоять у тестах — у назві, docstring чи
коментарі всередині тесту — і в полі `req` eval-кейсів. Скрипт збирає їх і пише
`docs/traceability.md`; `--check` падає, якщо файл застарів.

    uv run python scripts/trace.py
"""

import argparse
import ast
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("trace")

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "docs" / "requirements.md"
OUTPUT = ROOT / "docs" / "traceability.md"
EVAL = ROOT / "eval" / "qa.yaml"
# Тест самого сканера містить приклади ID — це не покриття вимог
SELF_TEST = "test_trace.py"

ID = re.compile(r"\b([RND]-\d{2})\b")
ROW = re.compile(r"^\| ([RND]-\d{2}) \| (.+?) \|")
# Скільки тестів показати в рядку; решта — числом
SHOWN = 4
# Вимоги, які перевіряються не тестом, а процесом чи документом
NOT_CODE = {
    "R-23": "звіт Мишці з результатами eval і прикладами відповідей",
    "N-04": "eval і звіт Мишці",
    "N-06": "рішення про стек — ADR",
    "N-01": "факт про архів: усі документи ≈ 1 сторінка",
    "N-02": "оцінка обсягу — враховано в лімітах і черзі",
    "N-07": "запас закладено рішенням D-23",
    "D-12": "сам набір eval/qa.yaml і scripts/eval_qa.py",
    "D-32": "тон текстів бота — texts.py",
}


@dataclass
class Trace:
    text: str
    tests: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    cases: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)


def requirements(path: Path = REQUIREMENTS) -> dict[str, Trace]:
    """R-*, N-*, D-* з таблиць requirements.md; суть — до першої крапки, «:» чи «;»."""
    found: dict[str, Trace] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if (match := ROW.match(line)) and match.group(1) not in found:
            text = re.sub(r"\*\*|`", "", match.group(2)).strip()
            if not text:
                continue
            short = re.split(r"(?<=[^\d])[.:;](?:\s|$)", text, maxsplit=1)[0]
            found[match.group(1)] = Trace(short if len(short) <= 90 else short[:89] + "…")
    return found


def _ids(text: str) -> set[str]:
    return set(ID.findall(text))


def scan_tests(root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """ID → тести, де він у назві, docstring чи тілі тесту; ID → модулі з ним у docstring."""
    tests: dict[str, list[str]] = defaultdict(list)
    modules: dict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("test_*.py")):
        if path.name == SELF_TEST:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        name = path.stem
        for requirement in _ids(ast.get_docstring(tree) or ""):
            modules[requirement].append(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test_"
            ):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                body = "\n".join(source.splitlines()[start - 1 : node.end_lineno])
                for requirement in _ids(body.replace("_", "-").upper()) | _ids(body):
                    tests[requirement].append(f"{name}::{node.name}")
    return tests, modules


def scan_eval(path: Path = EVAL) -> dict[str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases: dict[str, list[str]] = defaultdict(list)
    # Кейси до завантажень і після них (новий варіант від звіра, документ іншою мовою)
    for case in [*data["cases"], *data.get("after_uploads", {}).get("cases", [])]:
        for requirement in case.get("req", []):
            cases[requirement].append(case["id"])
    return cases


def scan_code(root: Path) -> dict[str, list[str]]:
    """ID → модулі коду, де він згаданий (коментар, docstring, текст бота)."""
    code: dict[str, list[str]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).with_suffix("").as_posix()
        for requirement in sorted(_ids(path.read_text(encoding="utf-8"))):
            code[requirement].append(module)
    return code


def collect(root: Path = ROOT) -> dict[str, Trace]:
    found = requirements(root / "docs" / "requirements.md")
    tests, modules = scan_tests(root / "tests")
    cases = scan_eval(root / "eval" / "qa.yaml")
    code = scan_code(root / "src" / "forest_bot")
    for requirement, trace in found.items():
        trace.tests = sorted(set(tests.get(requirement, [])))
        trace.modules = sorted(set(modules.get(requirement, [])))
        trace.cases = sorted(set(cases.get(requirement, [])))
        trace.code = code.get(requirement, [])
    return found


def _list(items: list[str], shown: int = SHOWN) -> str:
    text = ", ".join(f"`{i}`" for i in items[:shown])
    return f"{text} … ще {len(items) - shown}" if len(items) > shown else text


def _order(requirement: str) -> tuple[int, int]:
    return "RND".index(requirement[0]), int(requirement[2:])


def render(found: dict[str, Trace]) -> str:
    covered = [r for r, t in found.items() if t.tests or t.cases or t.modules]
    open_ = [r for r in sorted(found, key=_order) if r not in covered]
    lines = [
        "# Трасування вимог",
        "",
        "> Згенеровано `scripts/trace.py` з [requirements.md](requirements.md), тестів і "
        "`eval/qa.yaml`. Вручну не редагувати: `uv run python scripts/trace.py`.",
        "",
        f"Перевірено тестом чи eval-кейсом: **{len(covered)} з {len(found)}** вимог і рішень.",
        "",
        "| Вимога | Суть | Код | Тести | Eval |",
        "|---|---|---|---|---|",
    ]
    for requirement in sorted(found, key=_order):
        trace = found[requirement]
        tests = _list(trace.tests) or (f"модуль {_list(trace.modules)}" if trace.modules else "—")
        lines.append(
            f"| {requirement} | {trace.text.replace('|', '/')} | {_list(trace.code, 3) or '—'} "
            f"| {tests} | {_list(trace.cases, 3) or '—'} |"
        )
    lines += ["", "## Перевіряється не тестом", ""]
    for requirement in open_:
        reason = NOT_CODE.get(requirement, "немає тесту — див. обмеження в README")
        lines.append(f"- **{requirement}** {found[requirement].text} — {reason}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="лише перевірити, чи файл свіжий")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    text = render(collect())
    if args.check:
        fresh = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        if not fresh:
            logger.error("docs/traceability.md застарів — запусти scripts/trace.py")
        return 0 if fresh else 1
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    logger.info("Записано %s", OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
