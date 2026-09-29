from pathlib import Path
import re

from atlas.platforms import prune_rules, user_roots as _USER_ROOTS, project_roots as _PROJECT_ROOTS

DEFAULT_DEPTH = 9
TARGET_NAMES = {"agents.md", "claude.md", "gemini.md", "qwen.md", ".cursorrules",
                ".windsurfrules", ".clinerules", "copilot-instructions.md"}
PRUNE_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__",
              "site-packages", "dist", "build", "target", ".next", ".nuxt",
              ".turbo", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
              ".npm", ".cargo", ".rustup", ".local", "vendor", "third_party", "go", "Library"}
_PRUNE_DIRS = {".git", ".agentatlas", "node_modules", ".venv", "venv", "env", "__pycache__",
               "site-packages", "dist", "build", "target", ".next", ".nuxt",
               ".turbo", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
               "cache", "caches", ".npm", "vendor", "vendor_imports", "third_party",
               "tmp", ".tmp", "backups", ".curator_backups", "generated", "generated_images",
               "bin", "node", "hermes-agent", "worktrees", "browser", "chromium"}
_SENSITIVE_COMPONENT = re.compile(
    r"(?:^|[._-])(?:auth|oauth|credentials?|creds|tokens?|access[-_]?tokens?|refresh[-_]?tokens?|secrets?|passwords?|passwd|vault|keychain|private[-_]?key|api[-_]?key)(?:$|[._-])",
    re.IGNORECASE)


def is_sensitive_path(path):
    try:
        lexical = Path(path).expanduser().absolute()
        candidates = (lexical, lexical.resolve())
    except (OSError, RuntimeError, ValueError, TypeError):
        return True
    for candidate in candidates:
        parts = [part.lower() for part in candidate.parts]
        for index, part in enumerate(parts):
            if (part == ".env" or part.startswith(".env.") or _SENSITIVE_COMPONENT.search(part)
                    or part in {".ssh", ".aws", ".gnupg", "keys", "cookies", "login data"}
                    or part.startswith(("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"))
                    or part.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx"))
                    or (part in {"identity", "devices"} and any(p.startswith(".openclaw") for p in parts[:index]))):
                return True
    return False


def _pruned(relative, platform):
    extra, first_pruned, first_allowed = prune_rules(platform)
    for index, part in enumerate(relative.parts):
        name = part.lower()
        if index == 0 and name in first_allowed:
            continue
        if index == 0 and name in first_pruned:
            return True
        if (name in _PRUNE_DIRS or name in extra
                or name.endswith(("-cache", "_cache", "-browser-profile", "-backup"))
                or (platform == "cursor" and name == "extensions")):
            return True
    name = relative.name.lower()
    return (name == ".ds_store" or name.endswith((".lock", ".pyc", ".pyo", "~", "-wal", "-shm"))
            or re.search(r"(?:^|[.])(bak|backup|old)(?:[.]|$)", name) is not None
            or name.endswith((".min.js", ".min.css", ".map")))


def instruction_path_allowed(path, root=None, platform="shared"):
    path = Path(path)
    boundary = Path(root or Path.home())
    relative = path.relative_to(boundary) if path.is_relative_to(boundary) else path
    relative = Path(*(part for part in relative.parts if part not in {"worktrees", "hermes-agent"}))
    return not is_sensitive_path(path) and not _pruned(relative, platform)


def _instruction(path):
    return path.name.lower() in TARGET_NAMES or path.suffix.lower() == ".mdc"
