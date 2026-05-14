"""A2A Agent Logic - Handles TASK_REQUEST messages for development tasks.

Run as: python agent_logic.py "<task_json>"
Or:    python agent_logic.py task_name arg1=val1 arg2=val2

Supported tasks:
  status          - Git status, last commit, branch info
  code_review     - Review staged/committed changes
  run_tests       - Run pytest tests (optional: module=TESTS_PATH)
  lint            - Run ruff check on a module (optional: path=PATH)
  file_tree       - Show project file structure
  deps            - Show project dependencies
  graph_info      - Query graphify knowledge graph
  help            - List all tasks
"""

import json
import os
import subprocess
import sys
import textwrap


def run(cmd, timeout=30, cwd=None) -> dict:
    """Run a shell command and return output."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip()[:5000],
            "stderr": proc.stderr.strip()[:2000],
        }
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": f"Command not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


# ── Task Handlers ──────────────────────────────────────────────────────

def handle_status(_) -> dict:
    result = run(["git", "status", "--short"])
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    last_log = run(["git", "log", "--oneline", "-5"])
    return {
        "branch": branch.get("stdout", "unknown"),
        "changes": result.get("stdout", ""),
        "recent_commits": last_log.get("stdout", ""),
    }


def handle_code_review(payload: dict) -> dict:
    scope = payload.get("scope", "staged")
    if scope == "staged":
        diff = run(["git", "diff", "--cached"])
    elif scope == "working":
        diff = run(["git", "diff"])
    elif scope == "commit":
        ref = payload.get("ref", "HEAD~1..HEAD")
        diff = run(["git", "diff", ref])
    else:
        return {"error": f"Unknown scope: {scope}"}

    if not diff.get("stdout"):
        return {"review": "No changes to review."}

    diff_text = diff["stdout"]
    stats = run(["git", "diff", "--stat"])
    changed_files = len(stats.get("stdout", "").splitlines())

    # Count lines added/removed
    added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))

    # Estimate code quality flags
    flags = []
    for line in diff_text.splitlines():
        stripped = line.lstrip("+-")
        if "TODO" in stripped and line.startswith("+"):
            flags.append("new TODO added")
        if "print(" in stripped and line.startswith("+"):
            flags.append("new print() statement")
        if "import " in stripped and "os" in stripped and line.startswith("+"):
            pass

    return {
        "scope": scope,
        "changed_files": changed_files,
        "lines_added": added,
        "lines_removed": removed,
        "diff_preview": diff_text[:2000],
        "flags": list(set(flags)) if flags else None,
    }


def handle_run_tests(payload: dict) -> dict:
    module = payload.get("module", "")
    args = ["python", "-m", "pytest", "-v", "--tb=short", "-x"]
    if module:
        args.append(module)
    else:
        args.append("tests/")
    test_result = run(args, timeout=120)
    passed = test_result["stdout"].count("PASSED") if test_result["exit_code"] == 0 else 0
    failed = test_result["stdout"].count("FAILED")
    return {
        "exit_code": test_result["exit_code"],
        "passed": passed,
        "failed": failed,
        "output": test_result["stdout"][:2000] or test_result["stderr"][:2000],
    }


def handle_lint(payload: dict) -> dict:
    path = payload.get("path", ".")
    result = run(["python", "-m", "ruff", "check", "--quiet", path], timeout=30)
    return {
        "path": path,
        "issues": result["stdout"][:3000],
        "error_count": len(result["stdout"].splitlines()),
    }


def handle_file_tree(_) -> dict:
    result = run(["find", ".", "-not", "-path", "./.*", "-not", "-path", "./__pycache__/*",
                   "-not", "-path", "./*.egg-info/*", "-not", "-name", "*.pyc",
                   "-not", "-path", "./.venv/*", "-not", "-path", "./node_modules/*",
                   "-not", "-path", "./.git/*", "-maxdepth", 3])
    lines = result["stdout"].splitlines()
    return {"tree": lines[:80], "total_entries": len(lines)}


def handle_deps(_) -> dict:
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml") as f:
            content = f.read()
        deps = []
        in_deps = False
        for line in content.splitlines():
            if "dependencies" in line and "=" in line:
                in_deps = True
                continue
            if in_deps:
                stripped = line.strip().rstrip(",")
                if stripped and not stripped.startswith("["):
                    deps.append(stripped)
                if stripped.startswith("["):
                    break
        return {"dependencies": deps, "file": "pyproject.toml"}
    elif os.path.exists("requirements.txt"):
        with open("requirements.txt") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        return {"dependencies": lines[:50], "file": "requirements.txt"}
    return {"dependencies": [], "note": "No dependency file found"}


def handle_graph_info(_) -> dict:
    graph_dir = "graphify"
    if not os.path.isdir(graph_dir):
        return {"error": "graphify directory not found"}
    files = run(["find", graph_dir, "-name", "*.py"])
    stats = run(["wc", "-l"] + files["stdout"].splitlines()[:20]) if files["stdout"] else {"stdout": "0"}
    return {
        "modules": len(files["stdout"].splitlines()) if files["stdout"] else 0,
        "source_files": files["stdout"].splitlines()[:20],
    }


def handle_help(_) -> dict:
    return {
        "tasks": {
            "status": "Git branch, changes, recent commits",
            "code_review": "Review staged/working/commit changes (scope=staged|working|commit)",
            "run_tests": "Run pytest tests (module=path/to/test.py)",
            "lint": "Run ruff check (path=src/)",
            "file_tree": "Show project file structure",
            "deps": "Show project dependencies",
            "graph_info": "Show graphify module info",
            "help": "List all available tasks",
        }
    }


TASK_HANDLERS = {
    "status": handle_status,
    "code_review": handle_code_review,
    "run_tests": handle_run_tests,
    "lint": handle_lint,
    "file_tree": handle_file_tree,
    "deps": handle_deps,
    "graph_info": handle_graph_info,
    "help": handle_help,
}


def parse_args(args: list[str]) -> tuple[str, dict]:
    """Parse CLI args into (task_name, payload_dict)."""
    if not args:
        return "help", {}
    first = args[0]
    if first.startswith("{"):
        try:
            data = json.loads(first)
            return data.get("task", "help"), data.get("payload", {})
        except json.JSONDecodeError:
            return "help", {"error": "Invalid JSON"}
    task_name = first
    payload = {}
    for arg in args[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            payload[k] = v
    return task_name, payload


def main():
    task_name, payload = parse_args(sys.argv[1:])
    handler = TASK_HANDLERS.get(task_name)
    if not handler:
        result = {"error": f"Unknown task: {task_name}", "available": list(TASK_HANDLERS)}
    else:
        result = handler(payload)
    result["task"] = task_name
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
