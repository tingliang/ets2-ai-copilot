#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键接入「大肥鱼（DeepSeek-chan）」Live2D 模型。

用途：
    你已下载了神宫凉子免费配布的「DeepSeek免费公皮.zip」，但那个 zip 是
    VTube Studio 格式——model3.json 里没有声明 Motions/Expressions，
    我们的桌宠（Cubism 4 运行时）无法直接识别动作和表情。

    本脚本会从 zip 里提取模型，把中文文件名改成英文（避免路径编码问题），
    并补写一份带 Motions/Expressions/LipSync 声明的 model3.json，
    输出到 ui_pet/models/DeepSeekChan/。

用法（在项目根目录执行）：
    python ui_pet/setup_deepseek_chan.py "D:/下载/DeepSeek免费公皮.zip"
    不传路径时，脚本会自动在常见下载目录里找这个 zip。

注意（许可）：
    该模型由神宫凉子免费配布，允许个人使用、禁止重新分发。
    生成后的 DeepSeekChan/ 目录已被 .gitignore 排除，不会提交进 git。
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "models", "DeepSeekChan")

# 主文件：中文名 -> 英文名
MAIN_RENAME = {
    "大肥鱼.moc3": "model.moc3",
    "大肥鱼.physics3.json": "physics.physics3.json",
    "大肥鱼.cdi3.json": "cdi3.json",
    "大肥鱼.png": "preview.png",
}

# 贴图目录：中文目录 -> 英文目录
TEXTURE_DIR_RENAME = {"大肥鱼.4096": "textures"}

# 动作文件：中文名 -> 英文名
MOTION_RENAME = {
    "IDLE.motion3.json": "Idle.motion3.json",
    "举手切默认手.motion3.json": "RaiseHandBack.motion3.json",
    "狗尾草切回默认手.motion3.json": "DogtailBack.motion3.json",
    "狗尾草切换出来.motion3.json": "DogtailOut.motion3.json",
    "竖手指切出来动画.motion3.json": "FingergunOut.motion3.json",
    "竖手指切回默认.motion3.json": "FingergunBack.motion3.json",
    "脱捕.motion3.json": "TakeOff.motion3.json",
    "默认手切举手.motion3.json": "DefaultToRaise.motion3.json",
}

# 表情 Name 特殊覆盖（文件名 ＞＜ 显示为 ＞＜眼）
EXPR_NAME_OVERRIDE = {"＞＜": "＞＜眼"}


def find_zip(arg):
    if arg and os.path.isfile(arg):
        return arg
    candidates = []
    if arg:
        candidates.append(arg)
    candidates += [
        os.path.join(os.getcwd(), "DeepSeek免费公皮.zip"),
        os.path.join(HERE, "DeepSeek免费公皮.zip"),
        os.path.expanduser("~/Downloads/DeepSeek免费公皮.zip"),
        os.path.expanduser("~/Desktop/DeepSeek免费公皮.zip"),
        os.path.expanduser("~/下载/DeepSeek免费公皮.zip"),
        os.path.expanduser("~/桌面/DeepSeek免费公皮.zip"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def locate_model_root(extract_dir):
    """在解压目录里定位到包含 model3.json 的模型根目录。"""
    for root, _, files in os.walk(extract_dir):
        if "大肥鱼.model3.json" in files:
            return root
    return None


def build_model3(motion_groups, expressions):
    fr = {
        "Moc": "model.moc3",
        "Textures": [f"textures/texture_{i:02d}.png" for i in range(5)],
        "Physics": "physics.physics3.json",
        "DisplayInfo": "cdi3.json",
        "Motions": {
            name: [{"File": f"Animation/{fname}"}]
            for name, fname in sorted(motion_groups.items())
        },
        "Expressions": [
            {"Name": name, "File": f"Exp3/{fname}"}
            for name, fname in expressions
        ],
    }
    return {
        "Version": 3,
        "FileReferences": fr,
        "Groups": [
            {"Target": "Parameter", "Name": "EyeBlink",
             "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
            {"Target": "Parameter", "Name": "LipSync",
             "Ids": ["ParamMouthOpenY"]},
        ],
    }


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    zpath = find_zip(arg)
    if not zpath:
        print("[✗] 找不到 DeepSeek免费公皮.zip")
        print("    请把 zip 路径作为参数传入：")
        print('    python ui_pet/setup_deepseek_chan.py "D:/下载/DeepSeek免费公皮.zip"')
        sys.exit(1)

    print(f"[✓] 找到 zip: {zpath}")
    tmp = tempfile.mkdtemp(prefix="deepseek_chan_")

    try:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp)

        src = locate_model_root(tmp)
        if not src:
            print("[✗] 解压后没找到「大肥鱼.model3.json」，zip 结构不对？")
            sys.exit(1)

        if os.path.exists(OUT_DIR):
            shutil.rmtree(OUT_DIR)
        os.makedirs(os.path.join(OUT_DIR, "Animation"), exist_ok=True)
        os.makedirs(os.path.join(OUT_DIR, "Exp3"), exist_ok=True)

        # 1) 主文件
        for zh, en in MAIN_RENAME.items():
            shutil.copy2(os.path.join(src, zh), os.path.join(OUT_DIR, en))

        # 2) 贴图目录
        for zh, en in TEXTURE_DIR_RENAME.items():
            shutil.copytree(os.path.join(src, zh), os.path.join(OUT_DIR, en))

        # 3) 动作文件 + 动作组
        motion_groups = {}
        anim_src = os.path.join(src, "Animation")
        for zh, en in MOTION_RENAME.items():
            p = os.path.join(anim_src, zh)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(OUT_DIR, "Animation", en))
                motion_groups[en[:-len(".motion3.json")]] = en

        # 4) 表情文件 + 表情名
        expressions = []
        exp_src = os.path.join(src, "Exp3")
        for fn in sorted(os.listdir(exp_src)):
            if not fn.endswith(".exp3.json"):
                continue
            shutil.copy2(os.path.join(exp_src, fn),
                         os.path.join(OUT_DIR, "Exp3", fn))
            stem = fn[:-len(".exp3.json")]
            name = EXPR_NAME_OVERRIDE.get(stem, stem)
            expressions.append((name, fn))

        # 5) 写补好的 model3.json
        model3 = build_model3(motion_groups, expressions)
        with open(os.path.join(OUT_DIR, "model3.json"), "w", encoding="utf-8") as f:
            json.dump(model3, f, ensure_ascii=False, indent=2)

        print(f"[✓] 完成！输出目录: {OUT_DIR}")
        print(f"    动作组: {len(motion_groups)} 个 -> {sorted(motion_groups)}")
        print(f"    表情:   {len(expressions)} 个")
        print("    嘴型参数: ParamMouthOpenY（可张嘴说话）")
        print("    现在重启桌宠，右键菜单切到「大肥鱼」即可。")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
