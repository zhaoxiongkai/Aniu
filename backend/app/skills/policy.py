"""Skill classification, ordering, and prompt presentation rules."""
from __future__ import annotations

import os
import shutil

from app.skills.loader import SkillPackage

_IGNORED_SUPPORT_FILES = {"SKILL.md", "_meta.json"}
_PREFERRED_RUNTIME_TOOL_ORDER = [
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "glob",
    "grep",
    "exec",
    "web_search",
    "web_fetch",
    "http_get",
    "http_post",
]


def _truncate_items(values: list[str], *, limit: int = 4) -> list[str]:
    items = [str(value).strip() for value in values if str(value).strip()]
    if len(items) <= limit:
        return items
    return [*items[:limit], f"...(+{len(items) - limit})"]


def _list_support_files(pkg: SkillPackage, *, limit: int = 4) -> list[str]:
    files: list[str] = []
    for path in pkg.path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(pkg.path).as_posix()
        if relative in _IGNORED_SUPPORT_FILES or relative.startswith("__pycache__/"):
            continue
        files.append(relative)
    return _truncate_items(sorted(files), limit=limit)


def _format_missing_requirements(pkg: SkillPackage) -> str | None:
    requires = pkg.requires
    missing_bins = [
        item for item in requires.get("bins", []) if shutil.which(item) is None
    ]
    missing_envs = [
        item for item in requires.get("env", []) if not os.environ.get(item)
    ]
    details: list[str] = []
    if missing_bins:
        details.append("缺少命令: " + ", ".join(missing_bins))
    if missing_envs:
        details.append("缺少环境变量: " + ", ".join(missing_envs))
    return "; ".join(details) if details else None


class SkillPolicy:
    def is_system_runtime(self, pkg: SkillPackage) -> bool:
        return pkg.role == "runtime"

    def is_enabled(self, pkg: SkillPackage, disabled_ids: set[str]) -> bool:
        return pkg.always_enabled or pkg.id not in disabled_ids

    def tool_sort_key(self, pkg: SkillPackage) -> tuple[int, str]:
        return (0 if self.is_system_runtime(pkg) else 1, pkg.name.lower())

    def prompt_packages(
        self,
        packages: list[SkillPackage],
        *,
        run_type: str | None,
    ) -> list[SkillPackage]:
        normalized = str(run_type or "").strip()
        return [
            pkg
            for pkg in packages
            if not self.is_system_runtime(pkg) and pkg.supports_run_type(normalized)
        ]

    def runtime_tool_names(
        self,
        packages: list[SkillPackage],
        *,
        run_type: str | None,
    ) -> list[str]:
        normalized = str(run_type or "").strip() or "analysis"
        tool_names = {
            spec.get("function", {}).get("name", "")
            for pkg in packages
            if self.is_system_runtime(pkg)
            and pkg.skill is not None
            and pkg.supports_run_type(normalized)
            for spec in pkg.skill.tools_for(normalized)
            if spec.get("function", {}).get("name")
        }
        ordered = [name for name in _PREFERRED_RUNTIME_TOOL_ORDER if name in tool_names]
        extras = sorted(tool_names - set(ordered))
        from app.skills.runtime import _tool_allowed

        return [name for name in ordered + extras if _tool_allowed(name, normalized)]

    def skill_mode_label(self, pkg: SkillPackage, *, run_type: str) -> str:
        if pkg.skill is not None:
            tool_names = [
                spec.get("function", {}).get("name", "")
                for spec in pkg.skill.tools_for(run_type)
                if spec.get("function", {}).get("name")
            ]
            if tool_names:
                return "原生工具: " + ", ".join(_truncate_items(tool_names))
        return "文档驱动: 读取 `SKILL.md` 后配合运行时工具执行"

    def build_skill_summary_line(self, pkg: SkillPackage, *, run_type: str) -> str:
        details = [
            self.skill_mode_label(pkg, run_type=run_type),
            f"入口: `{pkg.skill_md_path.resolve()}`",
        ]
        missing = _format_missing_requirements(pkg)
        details.append(
            f"状态: 当前不可直接执行（{missing}）" if missing else "状态: 当前可用"
        )
        support_files = _list_support_files(pkg)
        if support_files:
            details.append(
                "支持文件: " + ", ".join(f"`{name}`" for name in support_files)
            )
        description = pkg.description or "无额外描述"
        return (
            f"- **{pkg.name}**（`{pkg.id}`）- {description}；"
            + "；".join(details)
        )
