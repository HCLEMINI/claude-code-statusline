import sys, json, re, os, glob, time, subprocess
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# ========== 5h quota background refresh mode ==========
# `python statusline.py --refresh-quota` fetches the GLM 5h quota from the
# API and writes the cache, then exits. Spawned detached by the main mode so
# the statusline display never blocks on the network call.
if '--refresh-quota' in sys.argv:
    try:
        home = os.path.expanduser('~')
        base_url, token = '', ''
        for p in (os.path.join(os.getcwd(), '.claude', 'settings.json'),
                  os.path.join(home, '.claude', 'settings.json')):
            try:
                with open(p, encoding='utf-8') as f:
                    env = json.load(f).get('env', {})
                base_url = base_url or env.get('ANTHROPIC_BASE_URL', '')
                token = token or env.get('ANTHROPIC_AUTH_TOKEN', '')
                if base_url and token:
                    break
            except Exception:
                continue
        quota_base = ('https://open.bigmodel.cn' if 'bigmodel.cn' in base_url.lower()
                      else 'https://api.z.ai')
        url = quota_base + '/api/monitor/usage/quota/limit'
        req = urllib.request.Request(url, headers={'Authorization': token, 'Accept': 'application/json'})
        resp = urllib.request.urlopen(req, timeout=15)
        body = json.loads(resp.read().decode('utf-8'))
        data = body.get('data', {}) or {}
        limits = data.get('limits', []) or []
        five_hour, weekly, unclassified = None, None, []
        for item in limits:
            if (item.get('type') or '').lower() != 'tokens_limit':
                continue
            pct = float(item.get('percentage') or 0)
            r = item.get('nextResetTime')
            try:
                r = int(r) if r is not None else None
            except Exception:
                r = None
            try:
                unit = int(item['unit']) if item.get('unit') is not None else None
            except Exception:
                unit = None
            entry = {'reset_ms': r, 'percentage': pct}
            if unit == 3 and five_hour is None:
                five_hour = entry
            elif unit == 6 and weekly is None:
                weekly = entry
            else:
                unclassified.append(entry)
        unclassified.sort(key=lambda e: (e['reset_ms'] is None, e['reset_ms'] or 0))
        for e in unclassified:
            if five_hour is None: five_hour = e
            elif weekly is None: weekly = e
            else: break
        if five_hour:
            cache = {
                'remaining_percent': round(100.0 - five_hour['percentage'], 2),
                'utilization_percent': round(five_hour['percentage'], 2),
                'reset_ms': five_hour['reset_ms'],
                'base_url': base_url,
                'fetched_at': time.time(),
            }
            with open(os.path.join(home, '.claude', 'quota_5h_cache.json'), 'w', encoding='utf-8') as f:
                json.dump(cache, f)
    except Exception:
        pass
    sys.exit(0)

# ========== Kimi balance background refresh mode ==========
# `python statusline.py --refresh-kimi-balance` fetches the Moonshot/Kimi
# account balance and writes the cache, then exits.
if '--refresh-kimi-balance' in sys.argv:
    try:
        home = os.path.expanduser('~')
        token = ''
        # Find the Kimi/Moonshot token (project settings take precedence)
        for p in (os.path.join(os.getcwd(), '.claude', 'settings.json'),
                  os.path.join(home, '.claude', 'settings.json')):
            try:
                with open(p, encoding='utf-8') as f:
                    env = json.load(f).get('env', {})
                if 'moonshot' in (env.get('ANTHROPIC_BASE_URL', '')).lower():
                    token = env.get('ANTHROPIC_AUTH_TOKEN', '')
                    break
            except Exception:
                continue
        if token:
            url = 'https://api.moonshot.cn/v1/users/me/balance'
            req = urllib.request.Request(url, headers={
                'Authorization': 'Bearer ' + token,
                'User-Agent': 'cc-switch/1.0',
                'Accept': 'application/json',
            })
            resp = urllib.request.urlopen(req, timeout=15)
            body = json.loads(resp.read().decode('utf-8'))
            data = body.get('data', {}) or {}
            balance = float(data.get('available_balance') or 0)
            cache = {
                'balance': round(balance, 4),
                'currency': data.get('currency') or 'CNY',
                'fetched_at': time.time(),
            }
            with open(os.path.join(home, '.claude', 'kimi_balance_cache.json'), 'w', encoding='utf-8') as f:
                json.dump(cache, f)
    except Exception:
        pass
    sys.exit(0)

# ========== DeepSeek balance background refresh mode ==========
# `python statusline.py --refresh-ds-balance` fetches the DeepSeek account
# balance and writes the cache, then exits.
if '--refresh-ds-balance' in sys.argv:
    try:
        home = os.path.expanduser('~')
        token = ''
        for p in (os.path.join(os.getcwd(), '.claude', 'settings.json'),
                  os.path.join(home, '.claude', 'settings.json')):
            try:
                with open(p, encoding='utf-8') as f:
                    env = json.load(f).get('env', {})
                if 'deepseek' in (env.get('ANTHROPIC_BASE_URL', '')).lower():
                    token = env.get('ANTHROPIC_AUTH_TOKEN', '')
                    break
            except Exception:
                continue
        if token:
            url = 'https://api.deepseek.com/user/balance'
            req = urllib.request.Request(url, headers={
                'Authorization': 'Bearer ' + token,
                'User-Agent': 'cc-switch/1.0',
                'Accept': 'application/json',
            })
            resp = urllib.request.urlopen(req, timeout=15)
            body = json.loads(resp.read().decode('utf-8'))
            infos = body.get('balance_infos') or []
            if infos:
                bi = infos[0]
                balance = float(bi.get('total_balance') or 0)
                cache = {
                    'balance': round(balance, 4),
                    'currency': bi.get('currency') or 'CNY',
                    'fetched_at': time.time(),
                }
                with open(os.path.join(home, '.claude', 'ds_balance_cache.json'), 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
    except Exception:
        pass
    sys.exit(0)

# ========== Model Pricing (元/百万Token · 人民币) ==========
_PRICING_DATA = {
    'glm':         {'cache_read': 1.30,  'input': 6.00,  'output': 24.00,
                    'names': ['GLM-5.1', 'glm-5.1', 'GLM-5',
                              'GLM-5.2', 'glm-5.2', 'GLM-5.2[1m]', 'glm-5.2[1m]']},
    'deepseek-pro': {'cache_read': 0.025, 'input': 3.00,  'output': 6.00, 'peak_mult': 2.0,
                     'names': ['DeepSeek V4 Pro', 'deepseek-v4-pro', 'deepseek-v4-pro[1m]',
                               'DeepSeek-V4-Pro', 'deepseek_v4_pro']},
    'deepseek-flash': {'cache_read': 0.02, 'input': 1.00,  'output': 2.00, 'peak_mult': 2.0,
                       'names': ['DeepSeek V4 Flash', 'deepseek-v4-flash', 'deepseek-v4-flash[1m]',
                                 'DeepSeek-V4-Flash', 'deepseek_v4_flash']},
    'kimi':        {'cache_read': 1.10,  'input': 6.50,  'output': 27.00,
                    'names': ['Kimi 2.6', 'kimi-k2.6', 'kimi-k2', 'Kimi-K2.6',
                              'moonshot-kimi-k2.6']},
    'kimi-k3':     {'cache_read': 2.00,  'input': 20.00, 'output': 100.00,
                    'names': ['Kimi K3', 'kimi-k3', 'kimi-k3[1m]', 'Kimi-K3',
                              'moonshot-kimi-k3']},
}
PRICING = {}
for _g, _d in _PRICING_DATA.items():
    for _n in _d['names']:
        PRICING[_n] = {k: v for k, v in _d.items() if k != 'names'}


def get_pricing(model_name):
    """Look up pricing by exact name, then partial/fuzzy match."""
    if not model_name:
        return None
    if model_name in PRICING:
        return PRICING[model_name]
    low = model_name.lower()
    for key, pricing in PRICING.items():
        if key.lower() in low:
            return pricing
    return None


def is_peak_beijing(ts_str):
    """True if ISO8601 UTC timestamp falls in Beijing peak hours (9-12, 14-18).

    DeepSeek 峰谷定价: 北京时间 9:00~12:00 与 14:00~18:00 价格为平时 2 倍。
    Missing/unparseable timestamps → off-peak (conservative).
    """
    if not ts_str:
        return False
    try:
        s = ts_str.strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.fromisoformat(s)
        bj = dt.astimezone(timezone(timedelta(hours=8)))
        h = bj.hour
        return (9 <= h < 12) or (14 <= h < 18)
    except Exception:
        return False


# State file
STATE_FILE = Path.home() / '.claude' / 'statusline_state.json'


def fix_json(raw):
    """Fix unescaped backslashes in JSON from Claude Code on Windows."""
    return re.sub(r'(?<!\\)\\(?![\"\\\/bfnrtu])', r'\\\\', raw)


def fmt_tokens(n):
    if n >= 1_000_000:
        return f'{n/1_000_000:.1f}M'
    elif n >= 1000:
        return f'{n/1000:.1f}K'
    else:
        return str(n)


def fmt_rmb(yuan):
    if yuan >= 1:
        return f'¥{yuan:.2f}'
    elif yuan >= 0.01:
        return f'¥{yuan:.3f}'
    else:
        return f'¥{yuan:.4f}'


# ========== ANSI color helpers ==========
# Set STATUSLINE_NOCOLOR=1 to disable (e.g. when piping output).
_NO_COLOR = bool(os.environ.get('STATUSLINE_NOCOLOR'))

# One Dark Modern truecolor palette — mirrors ~/.claude/themes/one-dark-modern.json
# so the status line reads as part of the same theme as the Claude Code UI.
# Values are SGR sequences (fg = "38;2;R;G;B"); clr() renders them verbatim,
# so 24-bit-supporting terminals (Windows Terminal) show exact One Dark hues.
_OD_BLUE = '38;2;97;175;239'       # claude   #61afef
_OD_BLUE_BOLD = '1;38;2;97;175;239'
_OD_CYAN = '38;2;86;182;194'       # shimmer  #56b6c2
_OD_GREEN = '38;2;152;195;121'     # success  #98c379
_OD_YELLOW = '38;2;229;192;123'    # warning  #e5c07b
_OD_RED = '1;38;2;224;108;117'     # error    #e06c75 (bold so danger stays loud)
_OD_PURPLE = '38;2;198;120;221'    # perm     #c678dd


def clr(s, code):
    """Wrap s in ANSI color; code is an SGR sequence (e.g. '32' or '38;2;97;175;239')."""
    if _NO_COLOR:
        return s
    return f'\033[{code}m{s}\033[0m'


def ctx_color(pct):
    """Green<50% / Yellow 50-80% / Red>=80% — context fill warning, in One Dark tones."""
    if pct is None:
        return _OD_CYAN
    if pct >= 80:
        return _OD_RED
    if pct >= 50:
        return _OD_YELLOW
    return _OD_GREEN


def load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except Exception:
        pass


def parse_transcript_cost(filepath, fallback_pricing, offset=0):
    """Parse one transcript JSONL.

    Returns (cost, total_input, total_output, cache_read_total, new_offset).
      - total_input = input_tokens + cache_creation + cache_read (all input)
      - cache_read_total = sum of cache_read_input_tokens (served from cache)
      - new_offset = bytes consumed (file size after the read)
    Dedup is keyed by the record's `uuid` — the only reliable "same API
    response" identifier. Token-count dedup is WRONG: distinct calls routinely
    share identical token counts (measured: 149 merged groups, 52% cost
    undercount). Records without a uuid are always counted.
    offset>0 = incremental parse of newly appended lines only (append-only
    transcripts; new lines are never duplicates, so no dedup needed there).
    Looks up pricing per-entry via the message.model field, falling back to
    the session's model pricing (handles mid-session model switches).
    """
    seen = set() if offset == 0 else None
    cost = 0.0
    ti = to = cr_total = 0
    new_offset = offset
    try:
        # errors='replace': a corrupt byte must never abort the loop mid-file,
        # or the partial parse + unadvanced offset would DOUBLE-COUNT those
        # lines on the next incremental pass.
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            if offset:
                f.seek(offset)
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                usage = rec.get('message', {}).get('usage', {})
                if 'input_tokens' not in usage:
                    continue
                fp = (usage.get('input_tokens', 0),
                      usage.get('cache_creation_input_tokens', 0),
                      usage.get('cache_read_input_tokens', 0),
                      usage.get('output_tokens', 0))
                if seen is not None:
                    # Dedup by uuid only (same response logged >1x); never by
                    # token counts, which collide across distinct calls.
                    uid = rec.get('uuid', '')
                    if uid:
                        if uid in seen:
                            continue
                        seen.add(uid)
                pricing = get_pricing(rec.get('message', {}).get('model', '')) or fallback_pricing
                if not pricing:
                    continue
                ci = fp[0] + fp[1]
                cr = fp[2]
                co = fp[3]
                mult = pricing.get('peak_mult', 1.0)
                if mult != 1.0 and not is_peak_beijing(rec.get('timestamp', '')):
                    mult = 1.0  # 峰谷模型: 非高峰按平时价
                cost += (ci * pricing['input'] + cr * pricing['cache_read'] + co * pricing['output']) / 1_000_000 * mult
                ti += ci + cr
                to += co
                cr_total += cr
            new_offset = f.tell()
    except Exception:
        pass  # on error, keep previous offset (safe: no double count)
    return cost, ti, to, cr_total, new_offset


def parse_or_reuse(fp_path, size, cached, fallback_pricing):
    """Return a fresh cache entry for fp_path given its current size.

    Three paths:
      1. Unchanged size + valid cache (v=3) → reuse as-is (O(1), no I/O).
      2. File grew + valid cache → incremental parse of new bytes only
         (O(new data)); force a full re-parse every `incs>=40` passes as
         insurance against in-place rewrites (e.g. compaction).
      3. New file / invalid cache / truncated → full parse with uuid dedup.
    """
    if cached and cached.get('v') == 3 and cached.get('size') == size:
        return cached
    try:
        if cached and cached.get('v') == 3 and size >= cached.get('offset', 0) \
                and cached.get('incs', 0) < 40:
            c, ti, to, cr, new_off = parse_transcript_cost(fp_path, fallback_pricing,
                                                           cached['offset'])
            return {'v': 3, 'size': size, 'offset': new_off,
                    'cost': cached.get('cost', 0) + c,
                    'ti': cached.get('ti', 0) + ti,
                    'to': cached.get('to', 0) + to,
                    'cr': cached.get('cr', 0) + cr,
                    'incs': cached.get('incs', 0) + 1}
    except Exception:
        pass  # corrupt/malformed cache entry → fall through to full re-parse
    c, ti, to, cr, new_off = parse_transcript_cost(fp_path, fallback_pricing, 0)
    return {'v': 3, 'size': size, 'offset': new_off,
            'cost': c, 'ti': ti, 'to': to, 'cr': cr, 'incs': 0}


def compute_session_cost(transcript_path, session_id, fallback_pricing, state):
    """Compute cumulative cost across main transcript + all agent transcripts.

    Uses per-file size caching: unchanged files reuse cached totals; only
    grown/new files are re-parsed. Returns (main_cost, agent_cost, main_ti,
    main_to, agent_ti, agent_to, total_cr) where total_cr is cumulative
    cache_read across main + agent (for cache hit rate).
    """
    file_cache = state.get('file_cache', {})
    # Discover all relevant files
    files = {'main': [], 'agent': []}
    if transcript_path and os.path.isfile(transcript_path):
        files['main'].append(transcript_path)
    if transcript_path and session_id:
        proj_dir = os.path.dirname(transcript_path)
        sub_dir = os.path.join(proj_dir, session_id, 'subagents')
        if os.path.isdir(sub_dir):
            for af in glob.glob(os.path.join(sub_dir, '**', 'agent-*.jsonl'), recursive=True):
                files['agent'].append(af)

    def tally(file_list):
        total_cost = 0.0
        total_ti = total_to = total_cr = 0
        for fp_path in file_list:
            try:
                size = os.path.getsize(fp_path)
            except Exception:
                continue
            nxt = parse_or_reuse(fp_path, size, file_cache.get(fp_path), fallback_pricing)
            file_cache[fp_path] = nxt
            total_cost += nxt['cost']
            total_ti += nxt['ti']
            total_to += nxt['to']
            total_cr += nxt['cr']
        return total_cost, total_ti, total_to, total_cr

    mc, mti, mto, mcr = tally(files['main'])
    ac, ati, ato, acr = tally(files['agent'])

    # Prune stale cache entries (files no longer present)
    all_files = set(files['main']) | set(files['agent'])
    state['file_cache'] = {k: v for k, v in file_cache.items() if k in all_files}
    return mc, ac, mti, mto, ati, ato, mcr + acr


# ========== Parse input ==========
# NOTE: background refresh modes defined later in the file call functions
# defined above; they must NOT reach this stdin-parse block. Guard it.
if '--refresh-all-sessions' not in sys.argv:
    try:
        raw = sys.stdin.read()
        d = json.loads(fix_json(raw))
    except Exception as e:
        try:
            with open(os.path.join(os.path.expanduser('~'), '.claude', 'statusline_debug.log'), 'a', encoding='utf-8') as dbg:
                dbg.write(f'--- {__import__("datetime").datetime.now()} ---\n')
                dbg.write(f'Error: {e}\n')
                dbg.write(f'Raw stdin: {repr(raw)}\n\n')
        except Exception:
            pass
        print('parse error')
        sys.exit(0)

# ========== All-sessions total cost background refresh mode ==========
# `python statusline.py --refresh-all-sessions` walks every transcript under
# ~/.claude/projects/ (main + subagents), parses costs with per-file size
# caching, and writes the grand total. Runs detached so the statusline never
# blocks (first full parse ~7000 files ≈ tens of seconds, once; afterwards
# only changed files re-parse).
if '--refresh-all-sessions' in sys.argv:
    try:
        home = os.path.expanduser('~')
        cache_path = os.path.join(home, '.claude', 'all_sessions_cache.json')
        cache = {}
        try:
            with open(cache_path, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
        per_file = cache.get('per_file', {})
        proj_root = os.path.join(home, '.claude', 'projects')
        all_files = glob.glob(os.path.join(proj_root, '**', '*.jsonl'), recursive=True)
        total = 0.0
        for fp in all_files:
            try:
                size = os.path.getsize(fp)
            except Exception:
                continue
            nxt = parse_or_reuse(fp, size, per_file.get(fp), None)
            per_file[fp] = nxt
            total += nxt['cost']
        # Prune entries for deleted files
        cur = set(all_files)
        per_file = {k: v for k, v in per_file.items() if k in cur}
        out = {'total_cost': round(total, 4), 'per_file': per_file, 'computed_at': time.time()}
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(out, f)
    except Exception:
        pass
    sys.exit(0)

line1 = []  # 实时状态: 模型 / 上下文 / 当前token / 难度
line2 = []  # 会话累计: 花费 / 缓存命中

# 1. Model name
model_id = d.get('model', {}).get('id', '')
model = d.get('model', {}).get('display_name', '')
if model:
    line1.append(clr(model, _OD_BLUE_BOLD))

# 2. Context window usage + remaining (with sticky cache to avoid streaming flicker)
cw = d.get('context_window', {})
used = cw.get('used_percentage')
ti_cur = cw.get('total_input_tokens', 0)
to_cur = cw.get('total_output_tokens', 0)
cws = cw.get('context_window_size', 0)

session_id = d.get('session_id', '')
transcript_path = d.get('transcript_path', '')
state = load_state()
if state.get('session_id') != session_id:
    state = {'session_id': session_id, 'file_cache': {}}

prev_ti = state.get('last_ti', 0)
is_transient = (used is None or used == 0) and prev_ti > 0
if is_transient:
    used = state.get('last_used')
    ti_cur = state.get('last_ti', 0)
    to_cur = state.get('last_to', 0)
    cws = state.get('last_cws', 0)
else:
    remain = cw.get('remaining_percentage')
    if remain is None and used is not None:
        remain = round(100 - used, 1)
    state['last_used'] = used
    state['last_remain'] = remain
    state['last_ti'] = ti_cur
    state['last_to'] = to_cur
    state['last_cws'] = cws

if used is not None:
    remain = state.get('last_remain')
    line1.append(f'上下文 {clr(f"{used:.0f}% (剩余{remain:.0f}%)", ctx_color(used))}')

# 3. Current context tokens
if ti_cur > 0 or to_cur > 0:
    token_str = f'↑{fmt_tokens(ti_cur)} ↓{fmt_tokens(to_cur)}'
    if cws > 0:
        token_str += f'/{fmt_tokens(cws)}'
    line1.append(clr(token_str, _OD_CYAN))

# 4. Cumulative cost + cache stats (main + agent) via transcript parsing
fallback = get_pricing(model) or get_pricing(model_id)
main_cost, agent_cost, mti, mto, ati, ato, total_cr = compute_session_cost(
    transcript_path, session_id, fallback, state)
save_state(state)

if agent_cost > 0:
    line2.append(f'花费{clr(f"{fmt_rmb(main_cost + agent_cost)}(含agent{fmt_rmb(agent_cost)})", _OD_YELLOW)}')
elif main_cost > 0:
    line2.append(f'花费{clr(fmt_rmb(main_cost), _OD_YELLOW)}')
elif fallback:
    line2.append(f'花费{clr("¥0", _OD_YELLOW)}')

# 4b. Cumulative token consumption split into input / output (main + agent)
in_tok = mti + ati
out_tok = mto + ato
if in_tok + out_tok > 0:
    line2.append(clr(f'输入{in_tok / 1_000_000:.2f}M/输出{out_tok / 1_000_000:.2f}M', _OD_CYAN))

# 5. Cumulative cache hit rate = cache_read / total_input (main + agent)
total_ti_all = mti + ati
if total_ti_all > 0:
    rate = total_cr / total_ti_all * 100
    line2.append(clr(f'缓存命中{rate:.0f}%', _OD_PURPLE))

# 6. GLM 5h quota (cached; background-refreshed when stale)
def get_5h_quota_display(model_name):
    """Only for GLM sessions. Read cache (instant); if stale, fire-and-forget
    a background refresh and return the stale value (or None)."""
    low = (model_name or '').lower()
    if 'glm' not in low:
        return None  # quota API is GLM-specific
    cache_path = os.path.join(os.path.expanduser('~'), '.claude', 'quota_5h_cache.json')
    cache = {}
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    now = time.time()
    fetched_at = cache.get('fetched_at', 0)
    # Cache stale (>1min) → kick off background refresh, but only if one
    # isn't already in flight (refreshing_at within 30s)
    if now - fetched_at > 60:
        if now - cache.get('refreshing_at', 0) > 30:
            cache['refreshing_at'] = now
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
            except Exception:
                pass
            try:
                flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
                subprocess.Popen([sys.executable, os.path.abspath(__file__), '--refresh-quota'],
                                 creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            except Exception:
                pass
    used = cache.get('utilization_percent')
    if used is None:
        return None
    # Format reset countdown
    reset_ms = cache.get('reset_ms')
    suffix = ''
    if reset_ms:
        delta_h = (reset_ms - now * 1000) / 3_600_000.0
        if delta_h > 0:
            suffix = f'({delta_h * 60:.0f}min)' if delta_h < 1 else f'({delta_h:.1f}h)'
    return f'5h已用{clr(f"{used:.0f}%{suffix}", _OD_CYAN)}'

quota_str = get_5h_quota_display(model)
if quota_str:
    line2.append(quota_str)

# 6b. Kimi account balance (cached; background-refreshed when stale)
def get_kimi_balance_display(model_name):
    """Only for Kimi sessions. Read cache (instant); if stale, fire-and-forget
    a background refresh and return the cached value (or None)."""
    low = (model_name or '').lower()
    if 'kimi' not in low:
        return None  # balance API is Kimi/Moonshot-specific
    cache_path = os.path.join(os.path.expanduser('~'), '.claude', 'kimi_balance_cache.json')
    cache = {}
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    now = time.time()
    if now - cache.get('fetched_at', 0) > 60:
        if now - cache.get('refreshing_at', 0) > 30:
            cache['refreshing_at'] = now
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
            except Exception:
                pass
            try:
                flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
                subprocess.Popen([sys.executable, os.path.abspath(__file__), '--refresh-kimi-balance'],
                                 creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            except Exception:
                pass
    balance = cache.get('balance')
    if balance is None:
        return None
    return f'余额{clr(f"¥{balance:.2f}", _OD_CYAN)}'

kimi_str = get_kimi_balance_display(model)
if kimi_str:
    line2.append(kimi_str)

# 6c. DeepSeek account balance (cached; background-refreshed when stale)
def get_ds_balance_display(model_name):
    """Only for DeepSeek sessions. Read cache (instant); if stale,
    fire-and-forget a background refresh and return the cached value."""
    low = (model_name or '').lower()
    if 'deepseek' not in low:
        return None
    cache_path = os.path.join(os.path.expanduser('~'), '.claude', 'ds_balance_cache.json')
    cache = {}
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    now = time.time()
    if now - cache.get('fetched_at', 0) > 60:
        if now - cache.get('refreshing_at', 0) > 30:
            cache['refreshing_at'] = now
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
            except Exception:
                pass
            try:
                flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
                subprocess.Popen([sys.executable, os.path.abspath(__file__), '--refresh-ds-balance'],
                                 creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            except Exception:
                pass
    balance = cache.get('balance')
    if balance is None:
        return None
    return f'余额{clr(f"¥{balance:.2f}", _OD_CYAN)}'

ds_str = get_ds_balance_display(model)
if ds_str:
    line2.append(ds_str)

# 6d. All-sessions grand total cost (cached; background-refreshed when stale)
def get_all_sessions_total():
    """Sum of every session's cost across ~/.claude/projects/ (main + agents).
    Read cache (instant); if stale (>5min) or absent, fire-and-forget a
    background full scan and return the cached total (or None)."""
    cache_path = os.path.join(os.path.expanduser('~'), '.claude', 'all_sessions_cache.json')
    cache = {}
    try:
        with open(cache_path, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    now = time.time()
    if now - cache.get('computed_at', 0) > 300:
        if now - cache.get('refreshing_at', 0) > 60:
            cache['refreshing_at'] = now
            try:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f)
            except Exception:
                pass
            try:
                flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
                subprocess.Popen([sys.executable, os.path.abspath(__file__), '--refresh-all-sessions'],
                                 creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
            except Exception:
                pass
    total = cache.get('total_cost')
    if total is None:
        return None
    return total

all_total = get_all_sessions_total()
if all_total is not None:
    line3 = [f'全部session累计: {clr(fmt_rmb(all_total), _OD_CYAN)}']
else:
    line3 = []

# 7. Effort level
eff = d.get('effort', {}).get('level', '')
if eff:
    line1.append(f'⚡{eff}')

# Three-line output: line1 = live state, line2 = session totals, line3 = all sessions
out_lines = [' | '.join(p) for p in (line1, line2, line3) if p]
print('\n'.join(out_lines) if out_lines else '')
