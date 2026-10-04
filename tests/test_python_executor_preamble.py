"""
The preamble injected into every generated script must run on its own.

A logger line once got inserted into the helper string, which has no `import
logging`, so every execute_python call died with NameError before reaching the
agent's code — and because a crashed script still returned a result, the failure
only showed up as missing documents.
"""

import json
import re
from pathlib import Path

import pytest

from tools.python_executor import execute_python

SOURCE = Path(__file__).resolve().parents[1] / "tools" / "python_executor.py"


def injected_blocks():
    src = SOURCE.read_text(encoding="utf-8")
    return re.findall(r"=\s*'''(.*?)'''", src, re.S)


def test_injected_code_does_not_use_the_module_logger():
    # `logging` is not imported inside the generated script.
    for i, block in enumerate(injected_blocks()):
        assert not re.search(r"^\s*log\s*=\s*logging\.", block, re.M), \
            f"injected block {i} creates a logger"
        assert not re.search(r"^\s*log\.(debug|info|warning|error|exception)\(", block, re.M), \
            f"injected block {i} calls the module logger"


def test_module_keeps_its_own_logger():
    src = SOURCE.read_text(encoding="utf-8")
    header = src.split("def execute_python", 1)[0]
    assert "log = logging.getLogger(__name__)" in header


def test_preamble_runs_and_helpers_are_available(tmp_path):
    code = (
        "from openpyxl import Workbook\n"
        "wb = Workbook(); ws = wb.active\n"
        "ws.append(['Item', 'Cost']); ws.append(['Design', 1000])\n"
        "_xl_header(ws[1][0])\n"
        "xl_add_total_row(ws, ['TOTAL', 1000])\n"
        "wb.save(str(OUTPUT_DIR / 'preamble_test.xlsx'))\n"
        "print('ok', ws.max_row)\n"
    )
    result = json.loads(execute_python(code, output_dir=tmp_path))
    assert result["exit_code"] == 0, result.get("stderr", "")[-500:]
    assert "ok 3" in result["stdout"]
    assert (tmp_path / "preamble_test.xlsx").exists()


def test_a_failing_script_reports_a_nonzero_exit_code(tmp_path):
    result = json.loads(execute_python("raise ValueError('boom')", output_dir=tmp_path))
    assert result["exit_code"] != 0
    assert "ValueError" in result["stderr"]
