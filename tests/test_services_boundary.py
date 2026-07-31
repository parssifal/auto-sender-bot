# tests/test_services_boundary.py
import ast
import pathlib

SERVICES_DIR = pathlib.Path(__file__).resolve().parent.parent / "core" / "services"


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_services_do_not_import_telegram_or_aiogram():
    offenders: dict[str, set[str]] = {}
    for path in SERVICES_DIR.glob("*.py"):
        bad = {m for m in _imports(path) if m.split(".")[0] in {"telegram", "aiogram"}}
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"services must not import telegram/aiogram: {offenders}"
