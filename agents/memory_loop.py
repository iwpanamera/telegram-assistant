import os
import re

_BASE = os.path.dirname(os.path.dirname(__file__))
_MEMORY_PATH = os.path.join(_BASE, "MEMORY.md")
_CONTEXT_DIR = os.path.join(_BASE, "context")

_SECTIONS = ["Voice", "Process", "People", "Projects", "Output", "Tools", "Goals"]


def read_memory() -> str:
    """Читает MEMORY.md и возвращает содержимое."""
    try:
        with open(_MEMORY_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def read_context() -> str:
    """Читает все файлы из context/ и возвращает их содержимое."""
    if not os.path.isdir(_CONTEXT_DIR):
        return ""
    parts = []
    for fname in sorted(os.listdir(_CONTEXT_DIR)):
        fpath = os.path.join(_CONTEXT_DIR, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        parts.append(f"### {fname}\n{content}")
            except Exception:
                pass
    return "\n\n".join(parts)


def update_memory(section: str, content: str):
    """Обновляет конкретную секцию в MEMORY.md."""
    memory = read_memory()
    if not memory:
        memory = "# MEMORY\n\n" + "\n\n".join(f"## {s}\n" for s in _SECTIONS)

    pattern = re.compile(
        rf"(## {re.escape(section)}\n)(.*?)(?=\n## |\Z)",
        re.DOTALL,
    )

    new_block = f"## {section}\n{content.strip()}\n"

    if pattern.search(memory):
        memory = pattern.sub(new_block, memory)
    else:
        memory = memory.rstrip() + f"\n\n## {section}\n{content.strip()}\n"

    with open(_MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(memory)
