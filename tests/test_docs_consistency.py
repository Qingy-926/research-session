"""Public documentation consistency checks.

Standard library only. Offline: no network requests, no writes to host caches.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
MANIFESTS = {
    "trae": ROOT / ".trae-plugin" / "plugin.json",
    "codex": ROOT / ".codex-plugin" / "plugin.json",
}
REPO_URL = "https://github.com/Qingy-926/research-session"
CHECKED_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "install.md",
    ROOT / "docs" / "versioning-release.md",
    ROOT / "contracts" / "README.md",
)
BACKTICK = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
SKILL_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
SKILL_LINK = re.compile(r"^skills/([^/]+)/SKILL\.md$")
EXTERNAL = re.compile(r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#]\S*)?$")
REPO_PATH_PREFIXES = ("docs/", "contracts/", "skills/", "scripts/", "references/",
                      "assets/", "tests/", ".github/", ".trae-plugin/", ".codex-plugin/", "./")
UPSTREAM_PROJECTS = (
    "HKUSTDial/Supervisor-Skills",
    "HaoYangJin/Research-workflow-Skill",
    "wanshuiyin/Auto-claude-code-research-in-sleep",
)
# Positive claims that require online evidence this tree does not have.
FORBIDDEN_CLAIMS = (
    "已上架",
    "已在 GitHub 发布",
    "已在GitHub发布",
    "已公开发布",
    "已发布",
    "所有用户可在线安装",
    "所有用户已可在线安装",
    "可从市场安装",
    "已在市场发布",
    "available on the marketplace",
    "already published",
    "installable from the marketplace",
)
STATUS_MARKERS = ("尚未", "候选", "not yet", "candidate")
# Same set as tests/test_public_core.py; no speculative additions.
# Split literals keep the public artifact free of the private tokens themselves.
FORBIDDEN_TOKENS = ("huawei" "-curated", "HUA" "WEI", "project-" "028", "Project-" "028",
                    "project-" "029", ".cc-" "switch", "gpt-" "6.2-sol", "gpt-" "5.6-sol",
                    "573" "21", "157" "21", "kind-nibbling-" "ladybug")


def read(path):
    return path.read_text(encoding="utf-8")


def skill_names():
    return {path.parent.name for path in SKILLS.glob("*/SKILL.md")}


def test_skill_inventory_matches_readme_capability_matrix():
    names = skill_names()
    assert len(names) == 16, sorted(names)
    assert "research-session" in names

    readme = read(ROOT / "README.md")
    mentioned = {token for token in BACKTICK.findall(readme) if SKILL_TOKEN.match(token)}
    mentioned |= {match.group(1) for match in
                  (SKILL_LINK.match(target) for target in LINK.findall(readme)) if match}
    assert len(mentioned) >= 10, sorted(mentioned)
    assert mentioned <= names, sorted(mentioned - names)


def test_manifests_and_skill_version_agree():
    manifests = {host: json.loads(read(path)) for host, path in MANIFESTS.items()}
    version = manifests["codex"]["version"]
    for host, manifest in manifests.items():
        assert manifest["version"] == version, host
        assert manifest["license"] == "Apache-2.0", host
        assert manifest["repository"] == REPO_URL, host
    assert manifests["trae"] == manifests["codex"]

    skill_text = read(SKILLS / "research-session" / "SKILL.md")
    assert "版本 v%s" % version in skill_text

    changelog = read(ROOT / "CHANGELOG.md")
    assert re.search(rf"^## \[{re.escape(version)}\](?:\s|$)", changelog, re.M), "missing current version section"


def test_only_current_version_declarations_are_bound():
    version = json.loads(read(MANIFESTS["codex"]))["version"]
    declarations = {
        "README.md": ("当前 **%s 本地候选**" % version, "Version **%s**" % version),
        "docs/install.md": ("当前 `%s`" % version, "技能文件声明的 `%s`" % version),
        "docs/versioning-release.md": ("当前源树为 **%s 本地候选**" % version,),
        "skills/research-session/SKILL.md": ("版本 v%s" % version,),
    }
    for relative, expected in declarations.items():
        text = read(ROOT / relative)
        for marker in expected:
            assert marker in text, (relative, marker)
    # Historical examples stay readable; they are not current-version claims.
    assert "research-session-6.3.0.zip" in read(ROOT / "docs" / "versioning-release.md")


def test_generated_release_manifest_uses_the_current_version():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "docs_package_release", ROOT / "tools" / "package_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    version = module.artifact_version(ROOT)
    files = module.collect_files(ROOT)
    payload = module.build_archive(ROOT, files)
    manifest = module.build_manifest(ROOT, version, files, payload)
    assert manifest["version"] == version
    assert manifest["archive"]["filename"] == "research-session-%s.zip" % version
    assert manifest["file_count"] == len(files)


def test_license_and_third_party_notices():
    license_text = read(ROOT / "LICENSE")
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text

    notices = read(ROOT / "THIRD_PARTY_NOTICES.md")
    for project in UPSTREAM_PROJECTS:
        assert project in notices, project


def test_contract_docs_reference_existing_files():
    contracts_readme = read(ROOT / "contracts" / "README.md")
    for name in ("provider-adapter.v1.json", "run-manifest.v1.json", "recovery.v1.json"):
        assert (ROOT / "contracts" / name).is_file(), name
        assert name in contracts_readme, name


def test_local_links_and_repo_paths_exist():
    for doc in CHECKED_DOCS:
        text = read(doc)
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://")):
                assert EXTERNAL.match(target), (doc.name, target)
                continue
            if target.startswith(("#", "mailto:")):
                continue
            relative = target.split("#", 1)[0]
            if not relative:
                continue
            assert (doc.parent / relative).resolve().exists(), (doc.name, target)

        for token in BACKTICK.findall(text):
            token = token.strip()
            if not token or any(char.isspace() for char in token):
                continue
            if any(char in token for char in "*?["):
                continue
            if token.startswith(("http://", "https://", "~", "%", "-")) or "://" in token:
                continue
            if not token.startswith(REPO_PATH_PREFIXES):
                continue
            assert (ROOT / token).exists(), (doc.name, token)


def test_no_unverified_release_claims():
    markdown_files = sorted(ROOT.rglob("*.md"))
    assert markdown_files
    hits = [(str(path.relative_to(ROOT)), claim) for path in markdown_files
            for claim in FORBIDDEN_CLAIMS if claim in read(path)]
    assert hits == []

    readme = read(ROOT / "README.md")
    assert any(marker in readme for marker in STATUS_MARKERS), "README lacks a status statement"


def test_public_docs_have_no_private_tokens():
    targets = sorted(ROOT.glob("*.md"))
    for directory in ("docs", ".github", "contracts"):
        targets += [path for path in sorted((ROOT / directory).rglob("*")) if path.is_file()]
    hits = [(str(path.relative_to(ROOT)), token) for path in targets
            for token in FORBIDDEN_TOKENS if token in read(path)]
    assert hits == []
