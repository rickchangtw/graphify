"""A2A Agent Logic - Handles TASK_REQUEST messages for development tasks.

Run as: python agent_logic.py '{"task":"status"}'
       python agent_logic.py '{"task":"code_generation","files":[{"path":"test.py","content":"..."}],"message":"Add test"}'
"""

import json
import os
import subprocess
import sys


def run(cmd, timeout=60, cwd=None) -> dict:
    """Run a shell command and return output."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.strip()[:10000],
            "stderr": proc.stderr.strip()[:3000],
        }
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": f"Command not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"exit_code": -1, "stdout": "", "stderr": str(e)}


def _gh_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        token = os.environ.get("INPUT_GITHUB_TOKEN", "")
    return token


def _repo_slug() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "rickchangtw/graphify")


def _setup_git() -> dict:
    """Configure git user and remote URL with token auth."""
    run(["git", "config", "user.name", "A2A Agent"])
    run(["git", "config", "user.email", "a2a@sentinel-arch.dev"])
    token = _gh_token()
    if token:
        slug = _repo_slug()
        run(["git", "remote", "set-url", "origin",
             f"https://x-access-token:{token}@github.com/{slug}.git"], timeout=10)
        r = run(["git", "config", "--get", "remote.origin.url"], timeout=5)
        configured_url = r["stdout"] if r["exit_code"] == 0 else ""
        return {"configured": bool(configured_url), "git_user_set": True}
    return {"configured": False, "git_user_set": True}


# ── Existing Handlers ──────────────────────────────────────────────────

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
        diff = run(["git", "diff", payload.get("ref", "HEAD~1..HEAD")])
    else:
        return {"error": f"Unknown scope: {scope}"}
    if not diff.get("stdout"):
        return {"review": "No changes to review."}
    added = sum(1 for l in diff["stdout"].splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff["stdout"].splitlines() if l.startswith("-") and not l.startswith("---"))
    stats = run(["git", "diff", "--stat"])
    changed_files = len(stats.get("stdout", "").splitlines())
    return {"changed_files": changed_files, "lines_added": added, "lines_removed": removed, "diff_preview": diff["stdout"][:3000]}


def handle_run_tests(payload: dict) -> dict:
    module = payload.get("module", "tests/")
    install_key = "/tmp/.a2a_deps_installed"
    if not os.path.exists(install_key):
        run(["pip", "install", "-e", ".", "-q"], timeout=120)
        run(["pip", "install", "pytest", "-q"], timeout=30)
        with open(install_key, "w") as f: f.write("1")
    r = run(["python", "-m", "pytest", "-v", "--tb=short", "-x", module], timeout=180)
    passed = r["stdout"].count("PASSED") + r["stdout"].count("passed")
    failed = r["stdout"].count("FAILED")
    errored = r["stdout"].count("ERROR")
    success = failed == 0
    return {"success": success, "exit_code": r["exit_code"], "passed": passed, "failed": failed, "errors": errored, "output": (r["stdout"] or r["stderr"])[:3000]}


def handle_lint(payload: dict) -> dict:
    path = payload.get("path", ".")
    r = run(["python", "-m", "ruff", "check", "--quiet", path], timeout=60)
    return {"path": path, "issues": r["stdout"][:5000], "error_count": len(r["stdout"].splitlines())}


def handle_file_tree(_) -> dict:
    r = run(["find", ".", "-not", "-path", "./.*", "-not", "-path", "./__pycache__/*",
             "-not", "-path", "./*.egg-info/*", "-not", "-name", "*.pyc",
             "-not", "-path", "./.venv/*", "-not", "-path", "./node_modules/*",
             "-not", "-path", "./.git/*", "-maxdepth", 3])
    return {"tree": r["stdout"].splitlines()[:80]}


def handle_deps(_) -> dict:
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml") as f:
            content = f.read()
        deps, in_deps = [], False
        for line in content.splitlines():
            if "dependencies" in line and "=" in line:
                in_deps = True
                continue
            if in_deps:
                s = line.strip().rstrip(",")
                if s and not s.startswith("["):
                    deps.append(s.strip("\"'[]"))
                if s.startswith("["):
                    break
        return {"dependency_count": len(deps), "dependencies": deps, "file": "pyproject.toml"}
    elif os.path.exists("requirements.txt"):
        with open("requirements.txt") as f:
            deps = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        return {"dependencies": deps[:50], "file": "requirements.txt"}
    return {"error": "No dependency file found"}


# ── New: code_generation ───────────────────────────────────────────────

def handle_code_generation(payload: dict) -> dict:
    files = payload.get("files", [])
    commit_message = payload.get("message", "feat: auto-generated code")
    branch_name = payload.get("branch", f"auto-gen-{os.urandom(4).hex()}")
    if not files:
        return {"error": "No files specified. Pass files=[{path, content}, ...]"}
    token = _gh_token()
    if not token:
        return {"error": "GITHUB_TOKEN not available"}
    errors = []
    for f in files:
        fpath = f.get("path", "")
        content = f.get("content", "")
        if not fpath:
            errors.append("Missing path in file entry")
            continue
        os.makedirs(os.path.dirname(fpath) or ".", exist_ok=True)
        try:
            with open(fpath, "w") as fh:
                fh.write(content)
        except Exception as e:
            errors.append(f"Failed to write {fpath}: {e}")
    if errors:
        return {"partial": True, "errors": errors, "files_written": len(files) - len(errors)}
    git_setup = _setup_git()
    r = run(["git", "checkout", "-b", branch_name])
    run(["git", "add", "-A"])
    r2 = run(["git", "commit", "-m", commit_message, "--allow-empty"])
    if r2["exit_code"] != 0:
        return {"error": f"Commit failed: {r2['stderr']}"}
    push = run(["git", "push", "origin", branch_name], timeout=30)
    if push["exit_code"] != 0:
        return {"error": f"Push failed: {push['stderr']}"}
    pr_title = payload.get("pr_title", commit_message)
    pr_body = payload.get("pr_body", "Auto-generated by A2A agent.\n\nFiles:\n" + "\n".join(f"- {f.get('path','?')}" for f in files))
    gh_auth = run(["gh", "auth", "status"], timeout=10)
    pr = run(["gh", "pr", "create", "--title", pr_title, "--body", pr_body, "--base", "v7"], timeout=30)
    pr_result = pr.get("stdout", "") or pr.get("stderr", "")
    result = {
        "success": True,
        "branch": branch_name,
        "files_written": len(files),
        "commit": r2.get("stdout", "") or r2.get("stderr", "")[:200],
    }
    if pr_result:
        result["pr"] = pr_result.strip()
    else:
        result["pr_error"] = pr.get("stderr", "empty response - check gh auth")[:200]
    result["gh_auth"] = gh_auth.get("stdout","")[:100] or gh_auth.get("stderr","")[:100]
    result["errors"] = errors if errors else None
    return result


# ── New: refactor ──────────────────────────────────────────────────────

def handle_refactor(payload: dict) -> dict:
    path = payload.get("path", ".")
    if not os.path.exists(path):
        return {"error": f"Path not found: {path}"}
    findings = []
    r = run(["python", "-m", "ruff", "check", "--select", "E,W,F,D", "--quiet", path], timeout=60)
    ruff_findings = len(r["stdout"].splitlines()) if r["stdout"] else 0
    if r["stdout"]:
        findings.append({"tool": "ruff", "issues": ruff_findings, "details": r["stdout"][:3000]})
    if not os.path.exists("/tmp/.a2a_refactor_tools_installed"):
        run(["pip", "install", "lizard", "vulture", "-q"], timeout=60)
        with open("/tmp/.a2a_refactor_tools_installed", "w") as f: f.write("1")
    for tool, check_cmd in [
        ("lizard", ["python", "-m", "lizard", "--languages", "python", "--exclude", ".venv", path]),
        ("vulture", ["python", "-m", "vulture", path, "--min-confidence", "80"]),
    ]:
        check_result = run(check_cmd, timeout=60)
        if check_result["exit_code"] == 0 and check_result["stdout"]:
            findings.append({"tool": tool, "output": check_result["stdout"][:3000]})
        elif check_result["stderr"]:
            findings.append({"tool": tool, "output": check_result["stderr"][:1000]})
    return {"path": path, "total_findings": ruff_findings, "checks": findings}


# ── New: auto_fix ──────────────────────────────────────────────────────

def handle_auto_fix(payload: dict) -> dict:
    path = payload.get("path", ".")
    commit_message = payload.get("message", "style: auto-fix lint issues")
    token = _gh_token()
    if not token:
        return {"error": "GITHUB_TOKEN not available"}
    if not os.path.exists("/tmp/.a2a_ruff_installed"):
        run(["pip", "install", "ruff", "-q"], timeout=30)
        with open("/tmp/.a2a_ruff_installed", "w") as f: f.write("1")
    r = run(["python", "-m", "ruff", "check", "--fix", "--quiet", path], timeout=60)
    diff = run(["git", "diff", "--stat"])
    has_changes = bool(diff.get("stdout"))
    remaining_issues = r["stdout"].strip()
    if has_changes:
        _setup_git()
    result = {
        "fix_applied": has_changes,
        "ruff_exit_code": r["exit_code"],
        "remaining_issues_count": len(remaining_issues.splitlines()) if remaining_issues else 0,
        "has_uncommitted_changes": has_changes,
    }
    if has_changes:
        run(["git", "add", "-A"])
        c = run(["git", "commit", "-m", commit_message, "--allow-empty"])
        run(["git", "push"], timeout=30)
        result["commit"] = c.get("stdout", "") or c.get("stderr", "")
        result["diff_files"] = run(["git", "diff", "HEAD~1..HEAD", "--stat"]).get("stdout", "")
    return result


# ── New: security_scan ─────────────────────────────────────────────────

def handle_security_scan(payload: dict) -> dict:
    findings = []
    deps_file = payload.get("file", "pyproject.toml")
    # Check known vulnerable patterns in files
    pattern_scan = run([
        "grep", "-rn",
        "--include=*.py",
        "-E", "(eval|exec|pickle\\.loads|yaml\\.load\\()",
        "."
    ], timeout=30)
    dangerous = []
    for line in pattern_scan.get("stdout", "").splitlines():
        if "test" not in line and "__pycache__" not in line:
            dangerous.append(line[:200])
    if dangerous:
        findings.append({"type": "dangerous_call", "count": len(dangerous), "matches": dangerous[:10]})
    # Install and run pip-audit
    install = run(["pip", "install", "pip-audit", "-q"], timeout=30)
    if install["exit_code"] == 0:
        audit = run(["python", "-m", "pip_audit", "--desc"], timeout=60)
        if audit["stdout"]:
            findings.append({"type": "pip_audit", "output": audit["stdout"][:3000]})
        else:
            findings.append({"type": "pip_audit", "output": audit["stderr"][:1000] or "No vulnerabilities found"})
    else:
        findings.append({"type": "pip_audit", "error": "Failed to install pip-audit"})
    # Check for known version issues in deps
    if os.path.exists("requirements.txt"):
        with open("requirements.txt") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        findings.append({"type": "dependency_list", "count": len(lines), "file": "requirements.txt"})
    return {"findings": findings, "total_checks": len(findings)}


def handle_help(_) -> dict:
    return {
        "tasks": {
            "status": "Git branch, changes, recent commits",
            "code_review": "Review staged/working/commit changes (scope=staged|working|commit)",
            "run_tests": "Run pytest tests (module=path/to/test.py)",
            "lint": "Run ruff check (path=src/)",
            "file_tree": "Show project file structure",
            "deps": "Show project dependencies",
            "code_generation": "Generate code files and open a PR",
            "refactor": "Analyze code for complexity/style/unused issues (path=PATH)",
            "auto_fix": "Run ruff --fix and commit changes (path=PATH)",
            "security_scan": "Scan for dependency vulnerabilities and dangerous patterns",
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
    "code_generation": handle_code_generation,
    "refactor": handle_refactor,
    "auto_fix": handle_auto_fix,
    "security_scan": handle_security_scan,
    "help": handle_help,
}


def parse_args(args: list[str]) -> tuple[str, dict]:
    if not args:
        return "help", {}
    first = args[0]
    if first.startswith("{"):
        try:
            data = json.loads(first)
            return data.get("task", "help"), {k: v for k, v in data.items() if k != "task"}
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
