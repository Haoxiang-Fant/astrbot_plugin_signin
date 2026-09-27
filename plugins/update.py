# -*- coding: utf-8 -*-
"""自动更新模块（3.0.0 首启清理）。

场景：3.0.0 的文件被替换进原 2.x 版本的插件目录后，目录里残留 2.x 的旧文件
（modules/、旧 data/、旧文档等）。本模块在 3.0.0 首次启动时：

  1. 读取插件根目录的 manifest.sha256（3.0.0 全部文件的 sha256 清单，构建期生成）；
  2. 遍历插件目录，与清单逐文件对比哈希：
     - 相对路径不在清单中          → 2.x 残留/无关文件，删除；
     - 在清单中且哈希一致          → 3.0.0 本体文件，保留；
     - 在清单中但哈希不一致        → 保留并记警告（可能是用户改动或拷贝损坏；
       本模块无源副本不可自动修复，删除会直接损坏安装，故仅报告）；
  3. 删除清空后的空目录（含 __pycache__）；
  4. 在数据目录写 upgrade_3_0_0.done 标记，此后不再运行（只此一次）。

安全边界：只清理插件本体目录（_PLUGIN_DIR），绝不触碰
data/plugin_data/astrbot_plugin_signin 中的用户数据。
"""
import hashlib
import os
import shutil

from astrbot.api import logger

NAME = "update"

from ..core import VERSION, _DATA_DIR, _PLUGIN_DIR  # noqa: E402

MARKER = "upgrade_3_0_0.done"
_MANIFEST = "manifest.sha256"


def register(core):
    # 激活条件：仅 3.0.0 版本 + 首次启动（数据目录无标记）
    if VERSION != "3.0.0":
        logger.info(f"[自动更新] 版本 {VERSION} ≠ 3.0.0，跳过清理")
        return
    marker = os.path.join(_DATA_DIR, MARKER)
    if os.path.exists(marker):
        return  # 已清理过（幂等）
    try:
        removed, warned = cleanup(_PLUGIN_DIR, os.path.join(_PLUGIN_DIR, _MANIFEST))
        if removed:
            logger.info(f"[自动更新] 已清理 {len(removed)} 个非 3.0.0 残留文件: {', '.join(removed[:20])}"
                        + ("…" if len(removed) > 20 else ""))
        for rel in warned:
            logger.warning(f"[自动更新] 文件与 3.0.0 清单哈希不一致（保留未动）: {rel}")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("3.0.0 cleanup done")
    except Exception as e:
        logger.error(f"[自动更新] 清理失败（下次启动重试）: {e}")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path):
    """读清单 → {相对路径(正斜杠): sha256}；清单文件缺失返回 None（不清理）"""
    if not os.path.exists(manifest_path):
        logger.warning(f"[自动更新] 缺少 {_MANIFEST}，跳过清理")
        return None
    manifest = {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            manifest[parts[1].strip().replace("\\", "/")] = parts[0].lower()
    return manifest


def cleanup(plugin_dir, manifest_path):
    """按清单清理插件目录。返回 (已删相对路径列表, 哈希不一致列表)。"""
    manifest = load_manifest(manifest_path)
    if manifest is None:
        return [], []
    removed, warned = [], []
    # 1) 文件级清理（__pycache__ 内的 .pyc 不在清单中，同样按未列出文件删除）
    for root, dirs, files in os.walk(plugin_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, plugin_dir).replace("\\", "/")
            if rel == _MANIFEST:
                continue  # 清单自身
            if rel not in manifest:
                try:
                    os.remove(full)
                    removed.append(rel)
                except OSError as e:
                    logger.error(f"[自动更新] 删除失败（跳过）: {rel}: {e}")
                continue
            try:
                if _sha256(full) != manifest[rel]:
                    warned.append(rel)  # 3.0.0 文件被改动/损坏：保留仅报告
            except OSError as e:
                logger.error(f"[自动更新] 哈希计算失败（跳过）: {rel}: {e}")
    # 2) 空目录清理（自底向上；不删插件根目录）
    for root, dirs, files in os.walk(plugin_dir, topdown=False):
        if os.path.abspath(root) == os.path.abspath(plugin_dir):
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass
    return removed, warned


def generate_manifest(plugin_dir, out_path=None):
    """工具函数：为插件目录生成 manifest.sha256（构建期调用；运行时不使用）"""
    out_path = out_path or os.path.join(plugin_dir, _MANIFEST)
    lines = []
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name == _MANIFEST:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, plugin_dir).replace("\\", "/")
            lines.append(f"{_sha256(full)}  {rel}")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sorted(lines)) + "\n")
    return len(lines)
