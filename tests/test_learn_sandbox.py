"""Hostile submissions to the Learn 'write' checkpoint. None may touch the host; each must come back as a failed attempt."""
import pytest

from learn.grader import grade_write

STAGE = 2  # quantity(ops)


def body(line: str) -> str:
    return f"def quantity(ops):\n    {line}\n    return 0\n"


@pytest.mark.parametrize("code", [
    "import os\n\ndef quantity(ops):\n    return 0\n",
    body("import os; os.system('true')"),
    body("__import__('os').system('true')"),
    body("open('/tmp/stillcounts-pwned', 'w').write('x')"),
    body("exec('1+1')"),
    body("eval('1+1')"),
    body("getattr(ops, '__class__')"),
    body("x = ().__class__.__base__.__subclasses__()"),
    body("x = (lambda: 0).__globals__"),
    body("x = 'a'.__class__"),
    body("x = '{0.__class__}'.format(ops)"),
    body("x = ops[0].gi_frame"),
    body("x = type(ops)"),
    body("print(globals())"),
])
def test_escape_attempts_are_refused_before_they_run(code, tmp_path):
    r = grade_write(STAGE, code, timeout=5)
    assert r["passed"] is False
    assert r["error"]


def test_a_file_write_does_not_reach_the_disk():
    from pathlib import Path
    target = Path("/tmp/stillcounts-pwned")
    target.unlink(missing_ok=True)
    grade_write(STAGE, body("open('/tmp/stillcounts-pwned', 'w').write('x')"), timeout=5)
    assert not target.exists()


def test_builtins_are_a_whitelist_even_if_the_source_check_is_bypassed():
    """Defence in depth: skip the AST check and call the worker directly. `open` and `__import__` do not exist."""
    from learn.grader import _write_worker
    r = _write_worker(STAGE, "def quantity(ops):\n    return len(open('/etc/hosts').read())\n")
    assert r["passed"] is False and "NameError" in r["error"]
    r = _write_worker(STAGE, "def quantity(ops):\n    return __import__('os').getpid()\n")
    assert r["passed"] is False and "NameError" in r["error"]


def test_memory_bomb_is_a_failed_attempt_not_a_crash():
    r = grade_write(STAGE, "def quantity(ops):\n    x = [0] * (10 ** 11)\n    return 0\n", timeout=10)
    assert r["passed"] is False


def test_the_legitimate_solution_still_passes():
    code = 'def quantity(ops):\n    return sum(o.value for o in ops if o.kind == "adjust")\n'
    assert grade_write(STAGE, code)["passed"] is True
