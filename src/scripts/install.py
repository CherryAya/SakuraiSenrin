"""
Author: SakuraiCora<1479559098@qq.com>
Date: 2026-02-25 16:44:54
LastEditors: SakuraiCora<1479559098@qq.com>
LastEditTime: 2026-02-26 17:27:03
Description: 安装脚本，Gemini 写的
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import skia

from src.logger import logger

FONT_DIR = Path("./data/font/")
LOCK_FILE = FONT_DIR / ".fonts_installed.lock"


def _ask_user_with_timeout(prompt: str, timeout: int = 5) -> bool:
    """带超时的终端交互询问"""
    sys.stdout.write(f"\n{prompt} [Y/n] (默认 {timeout} 秒后跳过): ")
    sys.stdout.flush()

    if sys.platform == "win32":
        try:
            return input().strip().lower() in ["", "y", "yes"]
        except EOFError:
            return False

    import select

    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
    if not rlist:
        logger.warning("\n⏳ 等待超时，自动跳过。")
        return False

    return sys.stdin.readline().strip().lower() in ["", "y", "yes"]


def _verify_with_skia(fonts: list[Path]) -> bool:
    """使用 Skia 引擎进行最终字体挂载验证"""
    logger.info("🔍 正在启动 Skia 引擎进行字体挂载验证...")
    font_mgr = skia.FontMgr()
    system_families = {
        font_mgr.getFamilyName(i) for i in range(font_mgr.countFamilies())
    }

    all_verified = True
    for font_path in fonts:
        try:
            tf = skia.Typeface.MakeFromFile(str(font_path))
            if not tf:
                logger.error(f"❌ Skia 无法解析文件: {font_path.name}")
                all_verified = False
                continue

            family_name = tf.getFamilyName()
            if family_name in system_families:
                logger.success(
                    f"✅ Skia 成功识别字体: '{family_name}' ({font_path.name})"
                )
            else:
                logger.warning(
                    f"⚠️ Skia 未能在系统缓存中找到: '{family_name}'。可能需要重启。"
                )
                all_verified = False
        except Exception as e:
            logger.error(f"❌ 验证字体 {font_path.name} 时发生异常: {e}")
            all_verified = False

    return all_verified


def _handle_windows(fonts: list[Path]) -> bool:
    """Windows 系统安装引导"""
    logger.warning("=" * 60)
    logger.warning("🪟 检测到 Windows 系统。为避免污染注册表，请手动安装字体！")
    logger.warning("👉 请在弹出的文件夹中全选字体 -> 右键 -> 选择【为所有用户安装】")
    logger.warning("=" * 60)

    if not _ask_user_with_timeout("准备好打开文件夹了吗？"):
        return False

    try:
        os.startfile(str(FONT_DIR.absolute()))  # type: ignore
    except Exception as e:
        logger.error(
            f"无法自动打开文件夹，请手动前往 {FONT_DIR.absolute()} 安装。报错: {e}"
        )

    if _ask_user_with_timeout(
        "是否已成功执行『右键安装』？(按 Y 进行 Skia 验证)", timeout=60
    ):
        return True

    return False


def _handle_linux(fonts: list[Path]) -> bool:
    """Linux 系统静默安装"""
    if not _ask_user_with_timeout("是否立即将这些字体安装到系统中？"):
        return False

    user_font_dir = Path.home() / ".local" / "share" / "fonts"
    user_font_dir.mkdir(parents=True, exist_ok=True)

    for font_path in fonts:
        logger.info(f"📦 正在复制: {font_path.name}...")
        try:
            target_path = user_font_dir / font_path.name
            if not target_path.exists():
                shutil.copy2(font_path, target_path)
        except Exception as e:
            logger.error(f"❌ 复制失败: {e}")
            return False

    logger.info("🔄 正在刷新 Linux 字体缓存 (fc-cache)...")
    try:
        subprocess.run(
            ["fc-cache", "-f", "-v"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning("⚠️ 未找到 fc-cache 命令，请确保已安装 fontconfig！")
        return False

    return True


def _handle_macos(fonts: list[Path]) -> bool:
    """macOS 系统静默安装"""
    if not _ask_user_with_timeout("是否立即将这些字体安装到系统中？"):
        return False

    user_font_dir = Path.home() / "Library" / "Fonts"
    user_font_dir.mkdir(parents=True, exist_ok=True)

    for font_path in fonts:
        logger.info(f"📦 正在复制: {font_path.name}...")
        try:
            target_path = user_font_dir / font_path.name
            if not target_path.exists():
                shutil.copy2(font_path, target_path)
        except Exception as e:
            logger.error(f"❌ 复制失败: {e}")
            return False

    return True


def init_fonts() -> None:
    """初始化与验证入口"""
    if LOCK_FILE.exists():
        return

    if not FONT_DIR.exists():
        logger.warning(f"⚠️ 字体目录 {FONT_DIR} 不存在，已跳过初始化。")
        return

    fonts = (
        list(FONT_DIR.glob("*.ttf"))
        + list(FONT_DIR.glob("*.otf"))
        + list(FONT_DIR.glob("*.ttc"))
    )
    if not fonts:
        return

    logger.info(f"✨ 检测到首次运行，找到 {len(fonts)} 个待安装的字体文件。")

    install_success = False
    if sys.platform == "win32":
        install_success = _handle_windows(fonts)
    elif sys.platform.startswith("linux"):
        install_success = _handle_linux(fonts)
    elif sys.platform == "darwin":
        install_success = _handle_macos(fonts)
    else:
        logger.warning(f"未知操作系统 {sys.platform}，请手动安装字体。")

    if not install_success:
        logger.warning("⚠️ 安装流程未完成，下次启动将自动重试。")
        return

    # 核心：交由 Skia 进行最终死刑核准
    if _verify_with_skia(fonts):
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.touch()
        logger.success("✅ 所有字体均已通过 Skia 引擎验证，环境初始化彻底完成！")
    else:
        logger.warning(
            "⚠️ Skia 验证未能全部通过，Lock 文件未生成。"
            "如果是 Windows 系统，请尝试重启机器人的命令行窗口。"
        )


if __name__ == "__main__":
    init_fonts()
