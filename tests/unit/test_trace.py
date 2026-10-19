"""Трасування вимог: ID з тестів і eval-кейсів, а таблиця в docs/ — свіжа."""

from pathlib import Path

from scripts.trace import OUTPUT, collect, render, requirements, scan_tests


def test_requirement_rows(tmp_path: Path) -> None:
    path = tmp_path / "requirements.md"
    path.write_text(
        "| R-03 | Усі звірі рівні: кожен і надсилає, і питає | L13 | ✅ |\n"
        "| D-05 | Видалити документ може лише той, хто його надіслав | Q-05 | ✅ |\n"
        "| R-03 | | |\n",
        encoding="utf-8",
    )
    found = requirements(path)
    assert {r: t.text for r, t in found.items()} == {
        "R-03": "Усі звірі рівні",
        "D-05": "Видалити документ може лише той, хто його надіслав",
    }


def test_ids_inside_a_test_count_but_not_its_neighbours(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        '"""Модуль про D-26."""\n\n'
        "def test_a() -> None:\n"
        "    # R-17: частина без рукавичок\n"
        "    assert True\n\n\n"
        "def helper() -> None:\n"
        "    # R-99 — не тест\n"
        "    pass\n\n\n"
        "def test_d_43_rollback() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    tests, modules = scan_tests(tmp_path)
    assert tests == {"R-17": ["test_x::test_a"], "D-43": ["test_x::test_d_43_rollback"]}
    assert modules == {"D-26": ["test_x"]}


def test_traceability_is_fresh() -> None:
    # Додав тест з ID вимоги — онови таблицю: uv run python scripts/trace.py
    assert Path(OUTPUT).read_text(encoding="utf-8") == render(collect())
