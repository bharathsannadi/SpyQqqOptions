"""broker.py imports alpaca-py lazily inside methods, so a renamed symbol only fails at
trade time. On 2026-11-17 `from alpaca.data.enums import ContractType` (gone in 0.45)
crashed every tick from the open until ~10:58 ET. Resolve every such import here instead."""
import ast
import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "spyqqq"


def _alpaca_imports():
    for py in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("alpaca"):
                for a in node.names:
                    yield pytest.param(node.module, a.name, id=f"{py.name}:{node.lineno}:{node.module}.{a.name}")


@pytest.mark.parametrize("module,name", list(_alpaca_imports()))
def test_lazy_alpaca_import_resolves(module, name):
    mod = importlib.import_module(module)
    assert hasattr(mod, name), f"{module} has no {name} in the installed alpaca-py"
