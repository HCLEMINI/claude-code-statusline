#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SessionStart hook: 保证 Claude Code 主题与终端底色始终为 One Dark Modern。

每次会话启动检测三处, 被改回纯黑/内置主题/丢失主题文件时自动恢复(自愈),
正确时静默退出(不阻塞启动)。设计依据见
Documents/1/Claude Code 主题与终端底色配置.md。
"""
import sys, json, os, glob

CLAUDE_SETTINGS = os.path.join(os.path.expanduser('~'), '.claude', 'settings.json')
THEME_FILE = os.path.join(os.path.expanduser('~'), '.claude', 'themes', 'one-dark-modern.json')
EXPECTED_THEME = 'custom:one-dark-modern'

# 护眼深灰底色白名单(配置文档第四节色阶 + 第五节社区方案)。
# background 落在此集合内 -> 尊重用户选择, 不动;
# 落在外(纯黑 #0C0C0C / #000000 / 其它非护眼色 / 被删除) -> 改回 #282c34。
EYE_CARE_BG = {
    '#282c34', '#1a1b26', '#1e2127', '#2c313a', '#30323d',  # 文档第四节
    '#1e1e2e', '#2e3440', '#282828',                         # Catppuccin/Nord/Gruvbox
}

THEME_JSON = {
    "name": "One Dark Modern",
    "base": "dark",
    "overrides": {
        "claude": "#61afef", "claudeShimmer": "#56b6c2", "success": "#98c379",
        "warning": "#e5c07b", "error": "#e06c75", "foreground": "#abb2bf",
        "muted": "#5c6370", "inactive": "#5c6370", "promptBorder": "#61afef",
        "permission": "#c678dd", "fastMode": "#e5c07b",
    },
}


def atomic_write_json(path, obj):
    """写 JSON: 先落临时文件再 os.replace, 防半写损坏宿主配置。"""
    tmp = path + '.tgtmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    fixed = []

    # 1. 主题文件存在性
    if not os.path.exists(THEME_FILE):
        os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
        atomic_write_json(THEME_FILE, THEME_JSON)
        fixed.append('主题文件已重建')

    # 2. Claude Code settings.json 的 theme 字段
    try:
        with open(CLAUDE_SETTINGS, encoding='utf-8') as f:
            cs = json.load(f)
        if cs.get('theme') != EXPECTED_THEME:
            old = cs.get('theme', '(空)')
            cs['theme'] = EXPECTED_THEME
            atomic_write_json(CLAUDE_SETTINGS, cs)
            fixed.append(f'Claude主题 {old}→{EXPECTED_THEME}')
    except Exception as e:
        # 读 settings 失败不阻塞启动, 仅提示
        sys.stderr.write(f'[theme_guard] 跳过 Claude settings: {e}\n')

    # 3. Windows Terminal 底色
    wt_glob = os.path.join(
        os.environ.get('LOCALAPPDATA', ''),
        'Packages', 'Microsoft.WindowsTerminal_*', 'LocalState', 'settings.json')
    for wt in glob.glob(wt_glob):
        try:
            with open(wt, encoding='utf-8') as f:
                w = json.load(f)
            defaults = w.setdefault('profiles', {}).setdefault('defaults', {})
            if not isinstance(defaults, dict):
                continue
            bg = defaults.get('background')
            if bg is None:
                defaults['background'] = '#282c34'
                atomic_write_json(wt, w)
                fixed.append('WT底色 未设置→#282c34')
            elif str(bg).lower() not in EYE_CARE_BG:
                defaults['background'] = '#282c34'
                atomic_write_json(wt, w)
                fixed.append(f'WT底色 {bg}→#282c34')
        except Exception:
            # WT 配置可能被占用或非标准 JSON, 跳过不阻塞
            pass

    if fixed:
        sys.stderr.write('[theme_guard] 已恢复 One Dark Modern: ' + '，'.join(fixed) + '\n')
    sys.exit(0)


if __name__ == '__main__':
    main()
