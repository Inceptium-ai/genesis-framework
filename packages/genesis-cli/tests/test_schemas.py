"""Versioned contract schemas: a real run_audit() report must validate against
readiness-report.schema.json, and task.schema.json must be a sound Draft 2020-12
schema that accepts a representative task."""

from __future__ import annotations

import json

from genesis_cli.adopt import run_audit
from genesis_gates.contracts import contracts_dir
from jsonschema import Draft202012Validator


def _load_schema(name: str) -> dict:
    schema = json.loads((contracts_dir() / name).read_text())
    Draft202012Validator.check_schema(schema)
    return schema


def _seed_repo(tmp_path):
    """Same synthetic repo as test_adopt: auth profile active, one env gap."""
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "demo", "scripts": {"test": "vitest", "build": "vite build"},
        "dependencies": {"next-auth": "^4.0.0"},
    }))
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  app:\n    environment:\n"
        "      API_SECRET_KEY: ${API_SECRET_KEY}\n"
        "      DB_HOST: ${DB_HOST:-postgres}\n"
    )
    src = tmp_path / "src" / "pages" / "api"
    src.mkdir(parents=True)
    (src / "signup.ts").write_text(
        "export default handler\n// POST /api/signup\n"
        "router.post('/api/auth/signup', signup)\n"
    )
    (tmp_path / "README.md").write_text("# demo\n")
    (tmp_path / ".gitignore").write_text("node_modules\n")
    return tmp_path


def test_run_audit_report_validates_against_schema(tmp_path):
    report = run_audit(_seed_repo(tmp_path))
    validator = Draft202012Validator(_load_schema("readiness-report.schema.json"))
    errors = sorted(validator.iter_errors(report),
                    key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    )


def test_task_schema_accepts_representative_task():
    validator = Draft202012Validator(_load_schema("task.schema.json"))
    task = {
        "id": "V1_T1",
        "title": "Implement user login",
        "batch": 2,
        "skills": ["backend"],
        "sources": {"wireframe": None, "integrations": [], "components": []},
        "user_story": "As a returning user I can sign in.",
        "acceptance_flows": {
            "happy_path": [{"step": 1, "user_action": "submit credentials",
                            "system": "returns session", "api_endpoint": "/api/login"}],
            "error_handling": [{"trigger": "wrong password", "system": "401",
                                "user_sees": "Invalid credentials"}],
        },
        "api_contracts": [{"operationId": "login", "method": "POST",
                           "path": "/api/login", "responses": {"200": {}}}],
        "ui_components": [{"name": "LoginForm", "behavior": "submits"}],
        "constraints": {"must_use": ["bcrypt"], "must_not": []},
        "files": {"create": [{"path": "api/login.py"}], "modify": []},
        "acceptance_criteria": ["A verified user can sign in"],
        "validation": [{"type": "file_exists", "path": "api/login.py"}],
    }
    assert not list(validator.iter_errors(task))
    # Fail-closed essentials still hold: bad id / empty criteria are rejected.
    assert list(validator.iter_errors({**task, "id": "T1"}))
    assert list(validator.iter_errors({**task, "acceptance_criteria": []}))
