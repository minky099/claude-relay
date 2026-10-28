# -*- coding: utf-8 -*-
"""
MemoRelay (Python 버전)

IPlus Chart 화면의 기존 칸(기본 탭 증상칸, 명세서메모) 위에 테두리 없는 창 두 개를 겹쳐 놓고
입력칸에 쓴 질문을 claude -p 로 보내 응답을 응답칸에 표시한다.
입력칸은 비어 있으면 완전히 투명하고 클릭이 통과해 아래 IPlus 증상칸을 그대로 쓸 수 있다.

실행: pythonw memorelay.py (또는 Start-MemoRelay-py.cmd). 설정: memo-relay.json, 말투: system-prompt.txt
처음 한 번 입력칸에 /login 으로 Claude CLI 로그인.
"""
import os
import sys
import json
import ctypes
import threading
import subprocess
import re
from ctypes import wintypes
from datetime import datetime

import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

# ---------- 경로 ----------
# PyInstaller exe 로 묶이면 __file__ 은 임시폴더를 가리키므로 exe 위치를 쓴다.
if getattr(sys, 'frozen', False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(ROOT, 'memo-relay.log')
CONFIG_FILE = os.path.join(ROOT, 'memo-relay.json')
PROMPT_FILE = os.path.join(ROOT, 'system-prompt.txt')
WORK_DIR = os.path.join(ROOT, 'work')
HIST_DIR = os.path.join(ROOT, 'history')


def log(msg):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


# ---------- 단일 인스턴스 ----------
_kernel32 = ctypes.windll.kernel32
_mutex = _kernel32.CreateMutexW(None, False, 'Local\\MemoRelay')
if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

# ---------- Win32 ----------
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

GWL_EXSTYLE = -20
GWL_HWNDPARENT = -8
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x1
LWA_ALPHA = 0x2
GW_CHILD = 5
WM_NCLBUTTONDOWN = 0xA1
HTCAPTION = 2

user32.GetWindowLongPtrW.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = user32.GetWindowLongPtrW.restype
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, user32.GetWindowLongPtrW.restype]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


def get_exstyle(hwnd):
    return int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))


def set_exstyle(hwnd, val):
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, user32.GetWindowLongPtrW.restype(val))


def set_owner(hwnd, owner):
    # 소유자를 IPlus 로 지정 -> IPlus 바로 위에만 뜨고 같이 최소화됨
    user32.SetWindowLongPtrW(hwnd, GWL_HWNDPARENT, user32.GetWindowLongPtrW.restype(owner))


def set_click_through(hwnd, on):
    ex = get_exstyle(hwnd)
    nx = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
    if nx != ex:
        set_exstyle(hwnd, nx)


def set_alpha(hwnd, opacity, colorkey=None):
    # opacity 0.0~1.0. 0 이면 완전히 투명(아래가 그대로 보임)
    # colorkey '#RRGGBB' 를 주면 그 색으로 칠해진 픽셀만 완전히 투명(클릭도 통과)
    ex = get_exstyle(hwnd)
    if not (ex & WS_EX_LAYERED):
        set_exstyle(hwnd, ex | WS_EX_LAYERED)
    flags, cref = LWA_ALPHA, 0
    if colorkey:
        r, g, b = int(colorkey[1:3], 16), int(colorkey[3:5], 16), int(colorkey[5:7], 16)
        flags, cref = LWA_ALPHA | LWA_COLORKEY, r | (g << 8) | (b << 16)
    user32.SetLayeredWindowAttributes(hwnd, cref, int(round(opacity * 255)), flags)


def client_origin(hwnd):
    p = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y


def is_iconic(hwnd):
    return bool(user32.IsIconic(hwnd))


def is_window(hwnd):
    return bool(user32.IsWindow(hwnd))


# 한글 조합 중인 글자는 IME 가 따로 그리는데, Tk 는 글꼴을 넘기지 않아 시스템 기본 글꼴로 보인다.
# 조합창에 입력칸과 같은 글꼴을 지정해 입력 중/입력 완료 글자가 같게 보이도록 한다.
imm32 = ctypes.windll.imm32
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmGetContext.argtypes = [wintypes.HWND]
imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
imm32.ImmSetCompositionFontW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]


class LOGFONTW(ctypes.Structure):
    _fields_ = [('lfHeight', ctypes.c_long), ('lfWidth', ctypes.c_long),
                ('lfEscapement', ctypes.c_long), ('lfOrientation', ctypes.c_long),
                ('lfWeight', ctypes.c_long), ('lfItalic', ctypes.c_byte),
                ('lfUnderline', ctypes.c_byte), ('lfStrikeOut', ctypes.c_byte),
                ('lfCharSet', ctypes.c_byte), ('lfOutPrecision', ctypes.c_byte),
                ('lfClipPrecision', ctypes.c_byte), ('lfQuality', ctypes.c_byte),
                ('lfPitchAndFamily', ctypes.c_byte), ('lfFaceName', ctypes.c_wchar * 32)]


def set_ime_font(hwnd, face, height_px):
    # height_px: 글자 높이(px). LOGFONT 는 음수면 행간 제외 글자 높이로 해석한다.
    lf = LOGFONTW()
    lf.lfHeight = -abs(int(height_px))
    lf.lfWeight = 400
    lf.lfCharSet = 1  # DEFAULT_CHARSET
    lf.lfFaceName = face[:31]
    himc = imm32.ImmGetContext(hwnd)
    if not himc:
        return
    try:
        imm32.ImmSetCompositionFontW(himc, ctypes.byref(lf))
    finally:
        imm32.ImmReleaseContext(hwnd, himc)


def start_drag(hwnd):
    # 테두리 없는 창을 마우스로 끌어 옮기기 (제목표시줄을 잡은 것처럼)
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)


_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_by_title_prefix(prefix):
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.startswith(prefix):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_EnumProc(cb), 0)
    return found[0] if found else None


# ---------- 설정 ----------
DEFAULTS = {
    'targetTitle': 'IPlus Chart',
    'model': 'sonnet',
    'claudeExe': '',
    'inputFont': 'NanumGothic',
    'inputFontSize': 9,
    'outputFont': 'NanumGothic',
    'outputFontSize': 9,
    # IPlus 클라이언트 영역 기준 좌표 (보정 모드로 맞춘 값이 저장됨)
    'input': {'x': 778, 'y': 116, 'w': 268, 'h': 140},
    'output': {'x': 1136, 'y': 326, 'w': 192, 'h': 134},
    'calibOpacity': 0.55,
    'hotkeyFocus': 'Ctrl+Alt+A',
    'hotkeyToggle': 'Ctrl+Alt+Z',
    'hotkeyCalibrate': 'Ctrl+Alt+F12',
    'saveHistory': True,
    'keepNumbers': True,
}


def read_config():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding='utf-8') as f:
                j = json.load(f)
            for k, v in j.items():
                if k in DEFAULTS:
                    cfg[k] = v
        except Exception as e:
            log('config read: %s' % e)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- claude -p ----------
def resolve_claude_exe(cfg, state):
    p = cfg.get('claudeExe')
    if p and os.path.exists(p):
        return p
    if state.get('exe') and os.path.exists(state['exe']):
        return state['exe']
    # 데스크톱 앱에 번들된 CLI (업데이트마다 버전 폴더가 바뀜)
    import glob
    pat = os.path.join(os.environ['LOCALAPPDATA'], 'Claude-Data', '*', 'claude-code', '*', '*', 'claude.exe')
    cands = glob.glob(pat)
    if cands:
        cands.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        state['exe'] = cands[0]
        return cands[0]
    return None


def clean_env():
    # API 키나 다른 Claude 세션의 인증 변수를 물려받지 않도록 제거 -> 항상 CLI 로그인(구독)으로
    return {k: v for k, v in os.environ.items()
            if not (k.upper().startswith('CLAUDE') or k.upper().startswith('ANTHROPIC'))}


MD_FENCE = re.compile(r'^\s*```')
MD_TABLE_SEP = re.compile(r'^[\s|:\-]+$')
MD_HEAD = re.compile(r'^\s*#{1,6}\s+')
MD_QUOTE = re.compile(r'^\s*>\s?')
MD_BULLET = re.compile(r'^\s*[-*+•·]\s+')
MD_NUM = re.compile(r'^\s*\d+[.)]\s+')
MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
MD_UND = re.compile(r'__(.+?)__')
MD_CODE = re.compile(r'`([^`]*)`')


def format_plain(t, keep_numbers):
    # 모델이 지침을 어기고 마크다운을 쓴 경우 메모 형태로 한 번 더 정리
    res = []
    blank = False
    for l in t.splitlines():
        if MD_FENCE.match(l):
            continue
        if MD_TABLE_SEP.match(l) and '--' in l:
            continue
        l = MD_HEAD.sub('', l)
        l = MD_QUOTE.sub('', l)
        l = MD_BULLET.sub('', l)
        if not keep_numbers:
            l = MD_NUM.sub('', l)
        l = MD_BOLD.sub(r'\1', l)
        l = MD_UND.sub(r'\1', l)
        l = MD_CODE.sub(r'\1', l)
        if l.lstrip().startswith('|'):
            l = '  '.join(c.strip() for c in l.strip().strip('|').split('|'))
        l = l.strip()
        if not l:
            if not blank and res:
                res.append('')
                blank = True
            continue
        res.append(l)
        blank = False
    return '\n'.join(res).strip()


# 입력칸 배경색. 이 색은 투명 처리되고, 입력한 글자 칸에만 흰 배경을 깔아 아래 글자를 가린다.
INPUT_KEY = '#FF00FE'


class App:
    def __init__(self):
        self.cfg = read_config()
        save_config(self.cfg)
        for d in (WORK_DIR, HIST_DIR):
            os.makedirs(d, exist_ok=True)

        self.owner = None
        self.owner_dirty = False
        self.hidden = False
        self.calibrating = False
        self.calib_backup = None
        self.files = []
        self.session_id = None
        self.hist_file = None
        self.proc = None
        self.result_holder = {}   # {'out':..., 'err':...} 백그라운드 스레드가 채움
        self.login_running = False
        self.login_result = None  # /login 이 끝나면 auth status 출력이 들어옴
        self.cancelled = False
        self.pending = None       # (question, files, model)
        self.out_text = ''
        self.exe = None
        self.state = {}

        self._build_ui()
        self._register_hotkeys()
        self.root.after(250, self._tick)
        log('started (python)')

    # ---------- UI ----------
    def _build_ui(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.in_win = tk.Toplevel(self.root)
        self.out_win = tk.Toplevel(self.root)
        for w, title, bg in ((self.in_win, '입력칸', INPUT_KEY), (self.out_win, '응답칸', 'white')):
            w.overrideredirect(True)      # 테두리/제목 없음
            w.title(title)
            w.configure(bg=bg)
            w.attributes('-topmost', False)

        self.in_text = tk.Text(self.in_win, wrap='word', bd=0, highlightthickness=0,
                               bg=INPUT_KEY, fg='black', undo=True,
                               font=(self.cfg['inputFont'], self.cfg['inputFontSize']))
        self.in_text.tag_configure('typed', background='white')
        self.in_text.tag_lower('typed')  # 선택 영역(sel) 색이 가려지지 않게
        self.in_text.pack(fill='both', expand=True)

        self.out_text_w = tk.Text(self.out_win, wrap='word', bd=0, highlightthickness=0,
                                  bg='white', fg='black', state='disabled',
                                  font=(self.cfg['outputFont'], self.cfg['outputFontSize']))
        self.out_text_w.pack(fill='both', expand=True)

        # 창 핸들 확보 (overrideredirect 후 hwnd)
        self.root.update_idletasks()
        self.in_hwnd = self._hwnd(self.in_win)
        self.out_hwnd = self._hwnd(self.out_win)
        for h in (self.in_hwnd, self.out_hwnd):
            ex = get_exstyle(h) | WS_EX_TOOLWINDOW | WS_EX_LAYERED
            set_exstyle(h, ex)

        # 이벤트
        self.in_text.bind('<Key>', self._on_in_key)
        self.in_text.bind('<KeyRelease>', lambda e: self._update_input_look())
        self.in_text.bind('<<Modified>>', self._on_in_modified)
        self.in_text.bind('<FocusIn>', self._on_in_focus)
        self.in_text.bind('<FocusOut>', lambda e: self._update_input_look())
        self.in_text.bind('<Button-1>', self._on_in_mousedown)
        self.out_text_w.bind('<Key>', self._on_out_key)
        self.out_text_w.bind('<Button-1>', self._on_out_mousedown)
        self.out_text_w.bind('<MouseWheel>', self._on_wheel)
        for w in (self.in_text, self.out_text_w):
            w.drop_target_register = None  # tkdnd 없음; 드롭은 클립보드/파일선택으로 대체

        self.in_win.withdraw()
        self.out_win.withdraw()

    def _hwnd(self, win):
        return user32.GetParent(win.winfo_id()) or win.winfo_id()

    # ---------- 입력칸 모습 ----------
    def _on_in_focus(self, e=None):
        self._apply_ime_font()
        self._update_input_look()

    def _apply_ime_font(self):
        # Tk 가 실제로 고른 글꼴/크기를 그대로 IME 조합창에 지정 (글꼴이 없어 대체돼도 둘이 같게)
        try:
            f = tkfont.Font(font=self.in_text.cget('font'))
            face = f.actual('family')
            size = f.actual('size')
            if size > 0:  # pt -> px
                px = round(size * float(self.root.tk.call('tk', 'scaling')))
            else:         # 음수면 이미 px
                px = -size
            set_ime_font(self.in_text.winfo_id(), face, px)
        except Exception as ex:
            log('ime font: %s' % ex)

    def _on_in_modified(self, e=None):
        # 글자가 들어가는 즉시(그려지기 전에) 배경을 칠해 깜박임이 없게
        if self.in_text.edit_modified():
            self.in_text.edit_modified(False)
            self._update_input_look()

    def _paint_typed(self):
        # 입력한 글자 칸만 흰 배경 -> insert(겹쳐쓰기)처럼 아래 글자가 한 칸씩 가려짐
        # 줄바꿈 문자는 빼야 줄 끝까지 흰색이 늘어나지 않는다
        t = self.in_text
        t.tag_remove('typed', '1.0', 'end')
        t.tag_add('typed', '1.0', 'end-1c')
        idx = '1.0'
        while True:
            idx = t.search('\n', idx, stopindex='end-1c')
            if not idx:
                break
            t.tag_remove('typed', idx)
            idx = t.index(idx + '+1c')

    def _update_input_look(self):
        # 배경은 항상 투명(컬러키), 입력한 글자 칸만 불투명. 비어 있고 커서도 없으면 클릭 통과
        if self.calibrating:
            return
        self._paint_typed()
        has_text = bool(self.in_text.get('1.0', 'end-1c'))
        focused = (self.root.focus_get() == self.in_text)
        set_alpha(self.in_hwnd, 1.0, INPUT_KEY)
        set_click_through(self.in_hwnd, not (has_text or focused))

    def show_output(self, t):
        self.out_text = t
        self.out_text_w.configure(state='normal')
        self.out_text_w.delete('1.0', 'end')
        self.out_text_w.insert('1.0', t)
        self.out_text_w.configure(state='disabled')
        self.out_text_w.see('1.0')
        self._sync_overlay()

    # ---------- IPlus 추적 ----------
    def _place(self, win, r, ox, oy):
        win.geometry('%dx%d+%d+%d' % (r['w'], r['h'], ox + r['x'], oy + r['y']))

    def _sync_overlay(self):
        if self.calibrating:
            return
        h = self.owner
        if not h or not is_window(h):
            h = find_by_title_prefix(self.cfg['targetTitle'])
        if h and (h != self.owner or self.owner_dirty):
            set_owner(self.in_hwnd, h)
            set_owner(self.out_hwnd, h)
            self.owner_dirty = False
        self.owner = h
        if not h or is_iconic(h) or self.hidden:
            self.in_win.withdraw()
            self.out_win.withdraw()
            return
        ox, oy = client_origin(h)
        self._place(self.in_win, self.cfg['input'], ox, oy)
        self._place(self.out_win, self.cfg['output'], ox, oy)
        if self.in_win.state() == 'withdrawn':
            self.in_win.deiconify()
        self._update_input_look()
        if self.out_text:
            if self.out_win.state() == 'withdrawn':
                self.out_win.deiconify()
        elif self.out_win.state() != 'withdrawn':
            self.out_win.withdraw()

    def switch_hidden(self):
        if self.calibrating:
            return
        self.hidden = not self.hidden
        self._sync_overlay()

    def enter_input(self):
        if self.calibrating:
            return
        if self.hidden:
            self.hidden = False
            self._sync_overlay()
        if self.in_win.state() != 'withdrawn':
            set_click_through(self.in_hwnd, False)
            user32.SetForegroundWindow(self.in_hwnd)
            self.in_text.focus_force()
            self._update_input_look()

    # ---------- claude 호출 ----------
    def _q(self, v):
        return '"' + v.rstrip('\\') + '"'

    def start_ask(self, text):
        exe = resolve_claude_exe(self.cfg, self.state)
        if not exe:
            self.show_output('오류: claude.exe 를 찾지 못함')
            return
        files = list(self.files)
        prompt = text
        if files:
            prompt += '\n\n첨부 파일 (Read 도구로 읽어서 참고):\n' + '\n'.join(files)

        argv = [exe, '-p', '--output-format', 'json', '--model', self.cfg['model'],
                '--system-prompt-file', PROMPT_FILE, '--tools', 'Read', '--allowedTools', 'Read',
                # 사용자 설정의 MCP 서버/플러그인/스킬 목록이 매번 수십만 토큰씩 실리는 것을 막음
                '--strict-mcp-config', '--setting-sources', 'project', '--disable-slash-commands']
        dirs = sorted(set(os.path.dirname(f) for f in files))
        for d in dirs:
            argv += ['--add-dir', d]
        if self.session_id:
            argv += ['--resume', self.session_id]

        env = clean_env()

        self.pending = (text, files, self.cfg['model'])
        self.cancelled = False
        self.result_holder = {}
        self.files = []

        def worker():
            try:
                cf = 0x08000000  # CREATE_NO_WINDOW
                p = subprocess.Popen(argv, cwd=WORK_DIR, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=cf)
                self.proc = p
                out, err = p.communicate(prompt.encode('utf-8'))
                self.result_holder = {
                    'out': out.decode('utf-8', 'replace'),
                    'err': err.decode('utf-8', 'replace'),
                    'code': p.returncode,
                }
            except Exception as e:
                self.result_holder = {'out': '', 'err': str(e), 'code': -1}
            finally:
                self.proc = None

        threading.Thread(target=worker, daemon=True).start()
        self.show_output('...')

    def complete_ask(self):
        r = self.result_holder
        self.result_holder = {}
        if self.cancelled:
            self.cancelled = False
            self.show_output('취소됨')
            return
        out, err = r.get('out', ''), r.get('err', '')
        line = None
        for l in out.splitlines():
            if l.lstrip().startswith('{'):
                line = l
        j = None
        if line:
            try:
                j = json.loads(line)
            except Exception:
                j = None
        if not j:
            log('bad output code=%s out=%s err=%s' % (r.get('code'), out, err))
            msg = ('%s\n%s' % (err, out)).strip()
            if re.search(r'Not logged in|/login', msg):
                msg += '\n\n입력칸에 /login 으로 로그인'
            self.show_output('오류: 응답을 읽지 못함\n' + msg)
            return
        if j.get('is_error'):
            msg = str(j.get('result', ''))
            if re.search(r'Not logged in|/login', msg):
                msg += '\n\n입력칸에 /login 으로 로그인'
            self.show_output('오류: ' + msg)
            return
        if j.get('session_id'):
            self.session_id = j['session_id']
        text = format_plain(str(j.get('result', '')), self.cfg['keepNumbers'])
        self.show_output(text)
        q, files, model = self.pending
        self.save_history(q, files, text, model)

    # ---------- CLI 로그인 ----------
    def start_login(self):
        # claude auth login 을 새 콘솔 창에서 실행 (브라우저가 열리고, 필요하면 코드 붙여넣기)
        if self.login_running:
            return
        exe = resolve_claude_exe(self.cfg, self.state)
        if not exe:
            self.show_output('오류: claude.exe 를 찾지 못함')
            return
        env = clean_env()
        self.login_running = True
        self.login_result = None

        def worker():
            try:
                subprocess.call([exe, 'auth', 'login'], cwd=WORK_DIR, env=env,
                                creationflags=0x00000010)  # CREATE_NEW_CONSOLE
                r = subprocess.run([exe, 'auth', 'status'], cwd=WORK_DIR, env=env,
                                   capture_output=True, creationflags=0x08000000, timeout=30)
                out = (r.stdout + r.stderr).decode('utf-8', 'replace').strip()
            except Exception as e:
                out = '오류: %s' % e
            self.login_result = out or '(상태 출력 없음)'

        threading.Thread(target=worker, daemon=True).start()
        self.show_output('로그인 창에서 진행')

    def complete_login(self):
        out = self.login_result
        self.login_result = None
        self.login_running = False
        log('login: %s' % out.replace('\n', ' '))
        self.show_output('로그인 상태\n' + out)

    def save_history(self, q, files, a, model):
        if not self.cfg['saveHistory']:
            return
        if not self.hist_file:
            self.hist_file = os.path.join(HIST_DIR, datetime.now().strftime('%Y-%m-%d_%H%M%S.md'))
        with open(self.hist_file, 'a', encoding='utf-8') as f:
            f.write('## %s  %s  session %s\n' % (datetime.now().strftime('%H:%M:%S'), model, self.session_id))
            f.write('Q: %s\n' % q)
            for fp in files:
                f.write('첨부: %s\n' % fp)
            f.write('\n%s\n\n' % a)

    # ---------- 파일 첨부 ----------
    def add_files(self, lst):
        for f in lst:
            f = str(f)
            if os.path.isfile(f) and f not in self.files:
                self.files.append(f)
        if self.files:
            names = ', '.join(os.path.basename(x) for x in self.files)
            self.show_output('첨부 %d: %s' % (len(self.files), names))

    def add_clipboard_files(self):
        # 파일 복사
        try:
            if user32.IsClipboardFormatAvailable(15):  # CF_HDROP
                data = self._clipboard_files()
                if data:
                    self.add_files(data)
                    return True
        except Exception as e:
            log('clip files: %s' % e)
        # 캡처 이미지
        try:
            img = self._clipboard_image()
            if img:
                self.add_files([img])
                return True
        except Exception as e:
            log('clip image: %s' % e)
        return False

    def _clipboard_files(self):
        shell32 = ctypes.windll.shell32
        if not user32.OpenClipboard(0):
            return []
        try:
            h = user32.GetClipboardData(15)  # CF_HDROP
            if not h:
                return []
            count = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
            res = []
            for i in range(count):
                need = shell32.DragQueryFileW(h, i, None, 0)
                buf = ctypes.create_unicode_buffer(need + 1)
                shell32.DragQueryFileW(h, i, buf, need + 1)
                res.append(buf.value)
            return res
        finally:
            user32.CloseClipboard()

    def _clipboard_image(self):
        # tkinter 로는 비트맵 접근이 어려워 PowerShell 로 저장 (pywin32/PIL 없이도 동작)
        path = os.path.join(WORK_DIR, datetime.now().strftime('paste_%Y%m%d_%H%M%S.png'))
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$i=[Windows.Forms.Clipboard]::GetImage();"
              "if($i){$i.Save('%s',[Drawing.Imaging.ImageFormat]::Png);'ok'}" % path.replace('\\', '\\\\'))
        try:
            r = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', ps],
                               capture_output=True, text=True,
                               creationflags=0x08000000, timeout=10)
            if 'ok' in r.stdout and os.path.exists(path):
                return path
        except Exception as e:
            log('ps image: %s' % e)
        return None

    def select_files(self):
        fs = filedialog.askopenfilenames()
        if fs:
            self.add_files(list(fs))

    # ---------- 입력 처리 ----------
    def invoke_input(self):
        t = self.in_text.get('1.0', 'end-1c').strip()
        m = re.match(r'^/(new|opus|sonnet|haiku|model|file|login|quit)(?:\s+(\S+))?$', t)
        if m:
            cmd, arg = m.group(1), m.group(2)
            self._clear_input()
            if cmd == 'new':
                self.session_id = None
                self.hist_file = None
                self.show_output('새 대화')
            elif cmd in ('opus', 'sonnet', 'haiku'):
                self.cfg['model'] = cmd
                save_config(self.cfg)
                self.show_output('모델: ' + self.cfg['model'])
            elif cmd == 'model':
                if arg:
                    self.cfg['model'] = arg
                    save_config(self.cfg)
                self.show_output('모델: ' + self.cfg['model'])
            elif cmd == 'file':
                self.select_files()
            elif cmd == 'login':
                self.start_login()
            elif cmd == 'quit':
                self.quit()
            return
        if self.proc:
            return
        if not t and not self.files:
            return
        if not t:
            t = '첨부 파일 내용 요약'
        self._clear_input()
        self.start_ask(t)

    def _clear_input(self):
        self.in_text.delete('1.0', 'end')
        self._update_input_look()

    def invoke_escape(self):
        if self.proc:
            self.cancelled = True
            try:
                self.proc.kill()
            except Exception:
                pass
        elif self.in_text.get('1.0', 'end-1c'):
            self._clear_input()
        elif self.files:
            self.files = []
            self.show_output('')
        else:
            self.show_output('')

    def _on_in_key(self, e):
        if self.calibrating:
            return self._calib_key(self.in_win, e)
        if e.keysym == 'Return':
            if e.state & 0x1:  # Shift
                return  # 줄바꿈 기본 동작
            self.invoke_input()
            return 'break'
        if e.keysym == 'Escape':
            self.invoke_escape()
            return 'break'
        if (e.state & 0x4) and e.keysym.lower() == 'v':  # Ctrl+V
            if self.add_clipboard_files():
                return 'break'
        if (e.state & 0x4) and e.keysym.lower() == 'o':  # Ctrl+O
            self.select_files()
            return 'break'

    def _on_out_key(self, e):
        if self.calibrating:
            return self._calib_key(self.out_win, e)
        if e.keysym == 'Escape':
            self.show_output('')
            return 'break'
        return 'break'  # 읽기전용

    def _on_wheel(self, e):
        self.out_text_w.yview_scroll(int(-e.delta / 120), 'units')
        return 'break'

    def _on_in_mousedown(self, e):
        if self.calibrating:
            start_drag(self.in_hwnd)
            return 'break'

    def _on_out_mousedown(self, e):
        if self.calibrating:
            start_drag(self.out_hwnd)
            return 'break'

    # ---------- 보정 모드 ----------
    def _iplus_origin(self):
        if self.owner:
            return client_origin(self.owner)
        return (0, 0)

    def start_calibrate(self):
        if self.calibrating:
            return
        ox, oy = self._iplus_origin()
        self.calib_backup = {
            'input': dict(self.cfg['input']),
            'output': dict(self.cfg['output']),
        }
        self.calibrating = True
        self.hidden = False
        specs = [(self.in_win, self.in_text, self.cfg['input'], '#FFD700'),
                 (self.out_win, self.out_text_w, self.cfg['output'], '#FF69B4')]
        for win, box, r, color in specs:
            self._place(win, r, ox, oy)
            win.configure(bg=color)
            box.configure(bg=color)
            hwnd = self._hwnd(win)
            set_click_through(hwnd, False)
            set_alpha(hwnd, float(self.cfg['calibOpacity']))
            win.attributes('-topmost', True)
            if win.state() == 'withdrawn':
                win.deiconify()
        self.out_text_w.configure(state='normal')
        user32.SetForegroundWindow(self.in_hwnd)
        self.in_text.focus_force()

    def stop_calibrate(self, save):
        if not self.calibrating:
            return
        ox, oy = self._iplus_origin()
        specs = [(self.in_win, self.in_text, self.cfg['input'], 'input'),
                 (self.out_win, self.out_text_w, self.cfg['output'], 'output')]
        for win, box, r, key in specs:
            if save:
                r['x'] = win.winfo_x() - ox
                r['y'] = win.winfo_y() - oy
                r['w'] = win.winfo_width()
                r['h'] = win.winfo_height()
            else:
                b = self.calib_backup[key]
                r.update(b)
            bg = INPUT_KEY if win is self.in_win else 'white'
            win.configure(bg=bg)
            box.configure(bg=bg)
            win.attributes('-topmost', False)
            set_alpha(self._hwnd(win), 1.0, INPUT_KEY if win is self.in_win else None)
        self.out_text_w.configure(state='disabled')
        self.calibrating = False
        self._update_input_look()
        self.owner_dirty = True
        if save:
            save_config(self.cfg)
            log('calibrated input=%s output=%s' % (self.cfg['input'], self.cfg['output']))
        self._sync_overlay()

    def _calib_key(self, win, e):
        if e.keysym == 'Escape':
            self.stop_calibrate(False)
            return 'break'
        step = 10 if (e.state & 0x4) else 1   # Ctrl -> 10px
        shift = bool(e.state & 0x1)
        dx = dy = 0
        if e.keysym == 'Left':
            dx = -step
        elif e.keysym == 'Right':
            dx = step
        elif e.keysym == 'Up':
            dy = -step
        elif e.keysym == 'Down':
            dy = step
        else:
            return
        if shift:
            w = max(10, win.winfo_width() + dx)
            h = max(10, win.winfo_height() + dy)
            win.geometry('%dx%d+%d+%d' % (w, h, win.winfo_x(), win.winfo_y()))
        else:
            win.geometry('+%d+%d' % (win.winfo_x() + dx, win.winfo_y() + dy))
        return 'break'

    # ---------- 전역 단축키 ----------
    def _register_hotkeys(self):
        # RegisterHotKey(hwnd=None) 은 WM_HOTKEY 를 "등록한 스레드"의 큐에 보낸다.
        # 그래서 메시지를 받는 스레드에서 등록까지 해야 한다 (tk 메인스레드가 아니라).
        self._hotkeys = {
            1: self.cfg['hotkeyToggle'],
            2: self.cfg['hotkeyCalibrate'],
            3: self.cfg['hotkeyFocus'],
        }
        threading.Thread(target=self._hotkey_loop, daemon=True).start()

    def _hotkey_loop(self):
        MOD = {'ctrl': 2, 'alt': 1, 'shift': 4, 'win': 8}
        VK = {'A': 0x41, 'Z': 0x5A, 'F12': 0x7B}
        for hid, combo in self._hotkeys.items():
            mod = 0
            vk = 0
            for part in combo.split('+'):
                p = part.strip()
                if p.lower() in MOD:
                    mod |= MOD[p.lower()]
                else:
                    vk = VK.get(p.upper(), 0)
            if not user32.RegisterHotKey(None, hid, mod | 0x4000, vk):  # MOD_NOREPEAT
                log('단축키 등록 실패: %s' % combo)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                hid = msg.wParam
                self.root.after(0, self._on_hotkey, hid)

    def _on_hotkey(self, hid):
        try:
            if hid == 1:
                self.switch_hidden()
            elif hid == 2:
                if self.calibrating:
                    self.stop_calibrate(True)
                else:
                    self.start_calibrate()
            elif hid == 3:
                self.enter_input()
        except Exception as e:
            log('hotkey: %s' % e)

    # ---------- 루프 ----------
    def _tick(self):
        try:
            if self.result_holder and self.proc is None:
                self.complete_ask()
            if self.login_result is not None:
                self.complete_login()
            self._sync_overlay()
        except Exception as e:
            log('tick: %s' % e)
        self.root.after(250, self._tick)

    def quit(self):
        try:
            for hid in (1, 2, 3):
                user32.UnregisterHotKey(None, hid)
        except Exception:
            pass
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        log('stopped')
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    try:
        App().run()
    except Exception as e:
        log('fatal: %s' % e)
        raise
