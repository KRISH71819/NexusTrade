"""
Codebase Packer Script
Compiles all source files, configurations, tests, and documentation (including .env)
into a single structured Markdown file `FULL_CODEBASE.md` for AI analysis.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".git",
    ".kilo",
    ".system_generated",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "venv",
    ".venv",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".ico",
    ".svg",
    ".zip",
    ".tar",
    ".gz",
}

EXCLUDE_FILES = {
    "package-lock.json",
    "FULL_CODEBASE.md",
    "BACKEND_CODEBASE.md",
    "BACKEND_CODEBASE.txt",
    "FULL_PROJECT_CODEBASE.md",
    "FULL_PROJECT_CODEBASE.txt",
    "BACKEND_V2.md",
    "FRONTEND_V2.md",
}

# Language mapping for markdown code fences
EXT_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".json": "json",
    ".css": "css",
    ".html": "html",
    ".md": "markdown",
    ".txt": "text",
    ".env": "bash",
    ".dockerignore": "text",
    "Dockerfile": "dockerfile",
}


def should_include_path(path: Path) -> bool:
    rel_parts = path.relative_to(REPO_ROOT).parts
    for part in rel_parts:
        if part in EXCLUDE_DIRS or part.startswith(".pytest"):
            return False
    if path.name == ".env" or (path.name.startswith(".env") and not path.name.endswith(".example")):
        return False
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix in EXCLUDE_EXTS:
        return False
    return True


def get_language(path: Path) -> str:
    if path.name == "Dockerfile":
        return "dockerfile"
    if path.name.startswith(".env"):
        return "bash"
    return EXT_LANG_MAP.get(path.suffix, "text")


def pack_files(collected_files: list, output_path: Path, title: str):
    print(f"Packing {len(collected_files)} files into {output_path.name}...")
    with open(output_path, "w", encoding="utf-8") as out:
        out.write(f"# {title}\n")
        out.write("> This document contains complete source files, configurations, and tests.\n\n")
        out.write("## Table of Contents\n\n")

        for f in collected_files:
            rel = f.relative_to(REPO_ROOT).as_posix()
            anchor = rel.replace("/", "").replace(".", "").replace("-", "").replace("_", "").lower()
            out.write(f"- [{rel}](#{anchor})\n")

        out.write("\n---\n\n")

        for f in collected_files:
            rel = f.relative_to(REPO_ROOT).as_posix()
            anchor = rel.replace("/", "").replace(".", "").replace("-", "").replace("_", "").lower()
            lang = get_language(f)

            out.write(f"## {rel}\n\n")
            out.write(f"```{lang}\n")
            try:
                content = f.read_text(encoding="utf-8")
                out.write(content)
                if not content.endswith("\n"):
                    out.write("\n")
            except Exception as e:
                out.write(f"# [Error reading file: {e}]\n")
            out.write("```\n\n---\n\n")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Successfully generated {output_path.name} ({size_mb:.2f} MB, {len(collected_files)} files)")


def pack_all():
    collected_files = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".pytest")]
        for file in files:
            full_path = Path(root) / file
            if should_include_path(full_path):
                collected_files.append(full_path)

    collected_files.sort(key=lambda p: str(p.relative_to(REPO_ROOT)))

    # 1. Full Master Codebase
    pack_files(collected_files, REPO_ROOT / "FULL_CODEBASE.md", "NEXUSTRADE AI (V2) — COMPLETE MASTER CODEBASE")

    # 2. Backend + Quant Core + Configs + Tests + .env
    backend_files = [f for f in collected_files if not str(f.relative_to(REPO_ROOT)).startswith("frontend")]
    pack_files(backend_files, REPO_ROOT / "BACKEND_V2.md", "NEXUSTRADE AI (V2) — BACKEND & QUANT ENGINE")

    # 3. Frontend Cockpit
    frontend_files = [f for f in collected_files if str(f.relative_to(REPO_ROOT)).startswith("frontend")]
    pack_files(frontend_files, REPO_ROOT / "FRONTEND_V2.md", "NEXUSTRADE AI (V2) — FRONTEND COCKPIT")


if __name__ == "__main__":
    pack_all()


