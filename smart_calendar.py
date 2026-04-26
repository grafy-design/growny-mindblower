#!/usr/bin/env python3
"""Turzx Smart Calendar v2 - 8.8" Ultra-Wide Smart Dashboard"""

import os
import sys
import io
import time
import datetime
import threading
import tempfile
import subprocess
import requests
import urllib.parse
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ─── In-memory log buffer + 파일 로깅 ────────────────────────────────────
_log_buffer = io.StringIO()
_log_lock = threading.Lock()
_LOG_MAX = 5000  # 최대 줄 수

# 파일 로그: 크래시 원인 추적용
_LOG_DIR = os.path.join(os.path.expanduser("~"), "TurzxLogs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, f"turzx_{datetime.datetime.now().strftime('%Y%m%d')}.log")

def _log(msg):
    """print 대체 — 콘솔 + 메모리 버퍼 + 파일 동시 기록."""
    line = f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line)
    with _log_lock:
        _log_buffer.write(line + "\n")
        # 버퍼 크기 제한
        if _log_buffer.tell() > 500_000:
            content = _log_buffer.getvalue()
            lines = content.splitlines()[-_LOG_MAX:]
            _log_buffer.seek(0)
            _log_buffer.truncate()
            _log_buffer.write("\n".join(lines) + "\n")
        # 파일 기록
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

def _open_log_viewer(icon=None, item=None):
    """로그를 임시파일에 쓰고 메모장으로 열기."""
    with _log_lock:
        content = _log_buffer.getvalue()
    if not content.strip():
        content = "(로그 없음)\n"
    tmp = os.path.join(tempfile.gettempdir(), "growny_mindblower_log.txt")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        subprocess.Popen(["notepad.exe", tmp])
    except Exception:
        pass

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Hardware
try:
    from library.lcd.lcd_comm_rev_c import LcdCommRevC
    from library.lcd.lcd_comm import Orientation
    HAS_DISPLAY = True
except ImportError:
    HAS_DISPLAY = False

# System tray
try:
    import pystray
    from pystray import MenuItem, Menu
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

# ─── Constants ────────────────────────────────────────────────────────────

SCREEN_WIDTH = 480
SCREEN_HEIGHT = 1920
COM_PORT = "COM4"

MATON_API_KEY = "6_uOrU5mwMx-uvZz59I8edXVXKuc25TSQpx1Z3w67U8UFiCyFJqARsPy0vCI268hC4fpmrJ220gfWf_JkVHhz0hDHEMGdeSiJH4"
GOV_API_KEY = "5592185f8b6d321587141f665d2b110c2a1a97eff2f504173ccb37267d4472e4"

NX, NY = 62, 126             # 광진구 기상청 격자
STATION_NAME = "광진구"       # 에어코리아 측정소

DISPLAY_REFRESH_SEC = 3
API_REFRESH_SEC = 5            # 캘린더 이벤트 갱신 주기
WEATHER_REFRESH_SEC = 900      # 날씨/미세먼지 갱신 주기 (15분)
TOAST_DURATION_SEC = 60
TOAST_CHECK_SEC = 60           # 1분마다 이메일 체크

# Colors
C_DARK1 = (26, 26, 46)
C_DARK2 = (22, 33, 62)
C_BG = (248, 249, 250)
C_WHITE = (255, 255, 255)
C_LIGHT = (200, 200, 220)
C_DIM = (140, 150, 170)
C_DARK_TEXT = (30, 30, 30)
C_GRAY_TEXT = (100, 100, 100)
C_NOW_RED = (224, 30, 90)
C_LINE = (225, 225, 230)
C_HOUR = (0, 0, 0)

# Layout
HEADER_H = 320
WEATHER_Y = 240
ALLDAY_ITEM_H = 50
ALLDAY_MAX = 6
HOUR_START = 8
HOUR_END = 24

# Toast
TOAST_H = 100
TOAST_PAD = 8
TOAST_MAX = 3
TOAST_MARGIN = 24
TOAST_AREA_H = 500             # 토스트 partial update 영역 높이
TOAST_AREA_Y = SCREEN_HEIGHT - TOAST_AREA_H  # 1420

# ─── Resource Helpers ─────────────────────────────────────────────────────

def _res(relative):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative)

_fcache = {}
_WEIGHT_NAMES = {
    300: "Light", 400: "Regular", 500: "Medium",
    600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}
def font(size, weight=400):
    key = (size, weight)
    if key not in _fcache:
        f = ImageFont.truetype(_res("fonts/PretendardVariable.ttf"), size)
        if weight != 400:
            try:
                f.set_variation_by_name(_WEIGHT_NAMES.get(weight, "Regular"))
            except Exception:
                try:
                    f.set_variation_by_axes([float(weight)])
                except Exception:
                    pass
        _fcache[key] = f
    return _fcache[key]

_ecache = {}
def emoji(name, size=36):
    key = (name, size)
    if key not in _ecache:
        path = _res(f"res/emoji/{name}.png")
        try:
            img = Image.open(path).convert("RGBA")
            if img.size != (size, size):
                img = img.resize((size, size), Image.LANCZOS)
            _ecache[key] = img
        except Exception as e:
            _log(f"[Emoji] Failed: {path} - {e}")
            fb = Image.new("RGBA", (size, size), (200, 200, 200, 255))
            _ecache[key] = fb
    return _ecache[key]

def hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _desaturate(rgb):
    """RGB → 채도 제거 (luminance 기반 grayscale, 약간 밝게 보정)."""
    g = int(rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114)
    g = min(255, g + 30)  # 너무 어둡지 않게
    return (g, g, g)

KST = datetime.timezone(datetime.timedelta(hours=9))

def now_kst():
    return datetime.datetime.now(KST)

# ─── API: Weather ─────────────────────────────────────────────────────────

WEATHER_MAP = {
    0: ("맑음", "weather_clear"),
    1: ("비", "weather_rain"),
    2: ("비/눈", "weather_rain"),
    3: ("눈", "weather_snow"),
    4: ("소나기", "weather_thunderstorm"),
    5: ("빗방울", "weather_rain"),
    6: ("비/눈", "weather_snow"),
    7: ("눈날림", "weather_snow"),
}

def fetch_weather():
    try:
        n = now_kst()
        base = n - datetime.timedelta(hours=1) if n.minute < 40 else n
        resp = requests.get(
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst",
            params={
                "serviceKey": GOV_API_KEY, "dataType": "JSON",
                "numOfRows": 10, "pageNo": 1,
                "base_date": base.strftime("%Y%m%d"),
                "base_time": base.strftime("%H00"),
                "nx": NX, "ny": NY,
            }, timeout=10,
        )
        obs = {i["category"]: i["obsrValue"]
               for i in resp.json()["response"]["body"]["items"]["item"]}
        pty = int(obs.get("PTY", 0))
        text, emj = WEATHER_MAP.get(pty, ("맑음", "weather_clear"))
        return {"temp": float(obs.get("T1H", 0)), "text": text, "emoji": emj}
    except Exception as e:
        _log(f"[Weather] {e}")
        return None

# ─── API: Air Quality ─────────────────────────────────────────────────────

GRADE_MAP = {
    1: ("좋음", "good"),
    2: ("보통", "moderate"),
    3: ("나쁨", "bad"),
    4: ("매우나쁨", "very_bad"),
}
GRADE_COLOR = {
    1: (70, 170, 255),
    2: (80, 200, 100),
    3: (255, 170, 40),
    4: (255, 60, 60),
}

def fetch_air():
    try:
        resp = requests.get(
            "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
            params={
                "serviceKey": GOV_API_KEY, "returnType": "json",
                "numOfRows": 5, "pageNo": 1,
                "stationName": STATION_NAME, "dataTerm": "DAILY", "ver": "1.5",
            }, timeout=10,
        )
        items = resp.json()["response"]["body"]["items"]
        # 최신 시간부터 유효한 pm10 데이터가 있는 항목 탐색
        for item in items:
            pm10 = item.get("pm10Value", "-")
            if pm10 and pm10 != "-":
                g10 = int(item.get("pm10Grade1h") or 0)
                g25 = int(item.get("pm25Grade1h") or 0)
                grade = max(g10, g25) or 2
                gt, ge = GRADE_MAP.get(grade, ("보통", "moderate"))
                return {
                    "pm10": pm10,
                    "pm25": item.get("pm25Value", "-"),
                    "grade": grade, "grade_text": gt, "grade_emoji": ge,
                }
        # 모든 항목이 "-"인 경우 최신 항목 그대로 반환
        item = items[0]
        return {
            "pm10": item.get("pm10Value", "-"),
            "pm25": item.get("pm25Value", "-"),
            "grade": 2, "grade_text": "보통", "grade_emoji": "moderate",
        }
    except Exception as e:
        _log(f"[Air] {e}")
        return None

# ─── API: Google Calendar ─────────────────────────────────────────────────

# Google Calendar 이벤트 colorId → 실제 hex 매핑
_EVENT_COLORS = {
    "1": "#7986CB",   # Lavender
    "2": "#33B679",   # Sage
    "3": "#8E24AA",   # Grape
    "4": "#E67C73",   # Flamingo
    "5": "#F6BF26",   # Banana
    "6": "#F4511E",   # Tangerine
    "7": "#039BE5",   # Peacock
    "8": "#616161",   # Graphite
    "9": "#3F51B5",   # Blueberry
    "10": "#0B8043",  # Basil
    "11": "#D50000",  # Tomato
}

def fetch_events():
    n = now_kst()
    sod = n.replace(hour=0, minute=0, second=0, microsecond=0)
    eod = n.replace(hour=23, minute=59, second=59, microsecond=999999)
    hdr = {"Authorization": f"Bearer {MATON_API_KEY}"}
    raw = []

    try:
        cals = requests.get(
            "https://gateway.maton.ai/google-calendar/calendar/v3/users/me/calendarList",
            headers=hdr, timeout=10,
        ).json().get("items", [])

        for cal in cals:
            if not (cal.get("selected") or cal.get("primary")):
                continue
            cid = urllib.parse.quote(cal["id"])
            cal_color = cal.get("backgroundColor", "#4285F4")
            evts = requests.get(
                f"https://gateway.maton.ai/google-calendar/calendar/v3/calendars/{cid}/events",
                headers=hdr, timeout=10,
                params={
                    "timeMin": sod.isoformat(), "timeMax": eod.isoformat(),
                    "singleEvents": "true", "orderBy": "startTime",
                },
            ).json().get("items", [])
            for e in evts:
                cid_ev = e.get("colorId")
                e["_color"] = _EVENT_COLORS.get(cid_ev, cal_color) if cid_ev else cal_color
            raw.extend(evts)
    except Exception as e:
        _log(f"[Calendar] {e}")
        return [], []

    allday, timed = [], []
    for ev in raw:
        sr = ev["start"].get("dateTime", ev["start"].get("date", ""))
        er = ev["end"].get("dateTime", ev["end"].get("date", ""))
        if "T" not in sr:
            allday.append({"summary": ev.get("summary", "제목 없음"), "color": ev.get("_color", "#4285F4")})
        else:
            sd = datetime.datetime.fromisoformat(sr.replace("Z", "+00:00")).astimezone(KST)
            ed = datetime.datetime.fromisoformat(er.replace("Z", "+00:00")).astimezone(KST)
            timed.append({
                "summary": ev.get("summary", "제목 없음"), "color": ev.get("_color", "#4285F4"),
                "start": sd, "end": ed,
                "time_str": f"{sd.strftime('%H:%M')} - {ed.strftime('%H:%M')}",
            })
    timed.sort(key=lambda e: e["start"])
    return allday, timed

# ─── API: Gmail ───────────────────────────────────────────────────────────

def fetch_emails():
    hdr = {"Authorization": f"Bearer {MATON_API_KEY}"}
    try:
        msgs = requests.get(
            "https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages",
            headers=hdr, timeout=10,
            params={"q": "in:inbox category:primary is:unread", "maxResults": 10},
        ).json().get("messages", [])

        emails = []
        for m in msgs[:5]:
            md = requests.get(
                f"https://gateway.maton.ai/google-mail/gmail/v1/users/me/messages/{m['id']}",
                headers=hdr, params={"format": "full"}, timeout=10,
            ).json()
            if "UNREAD" not in md.get("labelIds", []):
                continue
            hdrs = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
            sender = hdrs.get("From", "알 수 없음")
            if "<" in sender:
                sender = sender.split("<")[0].strip().strip('"')
            emails.append({
                "id": m["id"],
                "sender": sender,
                "subject": hdrs.get("Subject", "제목 없음"),
                "snippet": md.get("snippet", ""),
                "timestamp": int(md.get("internalDate", 0)) / 1000,
            })
        return emails
    except Exception as e:
        _log(f"[Gmail] {e}")
        return []

# ─── Toast Manager ────────────────────────────────────────────────────────

class ToastManager:
    """안 읽은 메일 → 슬라이드업 → 유지(60s) → 슬라이드아웃. ease-out 곡선."""

    ANIM_SEC = 3.0               # 애니메이션 총 3초
    MAX_OFFSET = 500             # 최대 오프셋 (화면 밖)

    def __init__(self):
        self.active = []
        self.phase = "none"      # none → in → show → out → done
        self.phase_start = 0

    def update(self, emails):
        if emails:
            self.active = emails[:TOAST_MAX]
            if not self.is_active:
                self.phase = "in"
                self.phase_start = time.time()
                _log(f"[Toast] slide-in {len(emails)} emails")
        else:
            if self.phase in ("in", "show"):
                self.phase = "out"
                self.phase_start = time.time()
                _log("[Toast] slide-out (all read)")

    @property
    def is_active(self):
        return self.phase not in ("none", "done")

    @property
    def animating(self):
        return self.phase in ("in", "out")

    @property
    def needs_body_refresh(self):
        """body partial에 토스트를 포함해서 갱신이 필요한 타이밍."""
        if not self.is_active:
            return False
        if self.animating:
            return True
        return False

    def tick(self):
        if not self.is_active:
            return [], 0

        now = time.time()
        elapsed = now - self.phase_start

        if self.phase == "in":
            # ease-out: offset = MAX * (1 - t/dur)^2
            t = min(elapsed / self.ANIM_SEC, 1.0)
            offset = int(self.MAX_OFFSET * (1 - t) ** 2)
            if t >= 1.0:
                self.phase = "show"
                self.phase_start = now
                offset = 0
            return self.active, offset

        if self.phase == "show":
            if elapsed >= TOAST_DURATION_SEC:
                self.phase = "out"
                self.phase_start = now
                _log("[Toast] duration expired, slide-out")
            return self.active, 0

        if self.phase == "out":
            # ease-in: offset = MAX * (t/dur)^2
            t = min(elapsed / self.ANIM_SEC, 1.0)
            offset = int(self.MAX_OFFSET * t ** 2)
            if t >= 1.0:
                self.phase = "done"
                self.active = []
                _log("[Toast] done")
            return self.active, offset

        return [], 0

# ─── Renderer ─────────────────────────────────────────────────────────────

# ─── Video Background ─────────────────────────────────────────────────────

# ─── 시간+날씨 기반 영상 시스템 ──────────────────────────────────────────
# 시간대별 영상 매핑: (start_hour, end_hour, sunny_file, rainy_file)
_VIDEO_SLOTS = [
    (3,  6,  "22-08_sunny.mp4", "03-10_rainy.mp4"),
    (6,  8,  "06-08_sunny.mp4", "03-10_rainy.mp4"),
    (8,  11, "08-11_sunny.mp4", "03-10_rainy.mp4"),
    (11, 13, "11-16_sunny.mp4", "10-13_rainy.mp4"),
    (13, 15, "11-16_sunny.mp4", "13-17_rainy.mp4"),
    (15, 17, "15-17_sunny.mp4", "13-17_rainy.mp4"),
    (17, 19, "17-19_sunny.mp4", "17-20_rainy.mp4"),
    (19, 20, "19-22_sunny.mp4", "17-20_rainy.mp4"),
    (20, 22, "19-22_sunny.mp4", "20-03_rainy.mp4"),
    (22, 27, "22-08_sunny.mp4", "20-03_rainy.mp4"),  # 22~03 (27=03+24)
]
_NIGHT_HOURS = set(range(19, 27)) | set(range(0, 7))  # 19~06 = dark

_video_frames = []
_video_idx = 0
_video_current_key = ""       # "sunny/08-11" 등 현재 로드된 키
_video_fade_frames = []       # 페이드 전환 프레임 큐
_video_fade_idx = 0
_video_loading = False        # 백그라운드 로딩 중

# 하단 그라데이션 오버레이 캐시
_header_gradient = None

def _build_header_gradient():
    """헤더 하단 검은색 그라데이션 오버레이 생성 (1회)."""
    global _header_gradient
    grad = Image.new("RGBA", (SCREEN_WIDTH, HEADER_H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(HEADER_H // 2, HEADER_H):
        t = (y - HEADER_H // 2) / (HEADER_H // 2)  # 0→1
        alpha = int(150 * t)
        gd.line([(0, y), (SCREEN_WIDTH, y)], fill=(0, 0, 0, alpha))
    _header_gradient = grad

def _get_video_slot(hour, is_rainy):
    """현재 시간+날씨로 영상 파일명과 키 반환."""
    h = hour if hour >= 3 else hour + 24  # 0~2시 → 24~26으로 매핑
    for s, e, sunny, rainy in _VIDEO_SLOTS:
        if s <= h < e:
            fname = rainy if is_rainy else sunny
            key = f"{'rainy' if is_rainy else 'sunny'}/{fname}"
            return fname, key
    # fallback
    return "22-08_sunny.mp4", "sunny/22-08_sunny.mp4"

def _is_night(hour):
    h = hour if hour >= 3 else hour + 24
    return h in _NIGHT_HOURS

def _load_video_file(filename=None):
    """영상 파일 프리로드. filename 없으면 기본 header_sunny.mp4."""
    global _video_frames, _video_idx
    if not HAS_CV2:
        _log("[Video] cv2 없음 — 정적 배경 사용")
        return []
    if filename:
        path = _res(f"mov/converted/{filename}")
        if not os.path.exists(path):
            path = _res(f"mov/{filename}")
    else:
        path = _res("mov/header_sunny.mp4")
    if not os.path.exists(path):
        _log(f"[Video] {path} 없음")
        return []
    cap = cv2.VideoCapture(path)
    forward = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        if pil.size != (SCREEN_WIDTH, HEADER_H):
            pil = pil.resize((SCREEN_WIDTH, HEADER_H), Image.LANCZOS)
        forward.append(pil)
    cap.release()
    if not forward:
        return []
    reverse = forward[-2:0:-1]
    frames = forward + reverse
    mem = len(frames) * SCREEN_WIDTH * HEADER_H * 3 / 1024 / 1024
    _log(f"[Video] {filename}: {len(forward)}fr → loop {len(frames)}fr ({mem:.0f}MB)")
    return frames

def _load_video():
    """초기 영상 로드."""
    global _video_frames, _video_idx, _video_current_key
    _build_header_gradient()
    n = now_kst()
    is_rainy = False  # 초기엔 sunny
    fname, key = _get_video_slot(n.hour, is_rainy)
    _video_frames = _load_video_file(fname)
    _video_idx = 0
    _video_current_key = key
    if not _video_frames:
        # fallback: header_sunny.mp4
        _video_frames = _load_video_file()
        _video_current_key = "fallback"

def _generate_fade_frames(fade_color, count=12):
    """페이드 프레임 생성. fade_color=(R,G,B), count=프레임수."""
    frames = []
    solid = Image.new("RGB", (SCREEN_WIDTH, HEADER_H), fade_color)
    # 현재 재생 중인 프레임에서 fade-out
    if _video_frames:
        cur = _video_frames[_video_idx % len(_video_frames)]
    else:
        cur = Image.new("RGB", (SCREEN_WIDTH, HEADER_H), (0, 0, 0))
    for i in range(count):
        alpha = (i + 1) / count  # 0→1 (점점 단색)
        blended = Image.blend(cur, solid, alpha)
        frames.append(blended)
    return frames, fade_color

def _generate_fadein_frames(new_frames, fade_color, count=12):
    """새 영상 첫 프레임으로 fade-in. fade_color→영상."""
    frames = []
    solid = Image.new("RGB", (SCREEN_WIDTH, HEADER_H), fade_color)
    target = new_frames[0] if new_frames else solid
    for i in range(count):
        alpha = (i + 1) / count  # 0→1 (점점 영상)
        blended = Image.blend(solid, target, alpha)
        frames.append(blended)
    return frames

def check_video_switch(weather_emoji):
    """날씨/시간 변경 시 영상 전환. 메인 루프에서 호출."""
    global _video_frames, _video_idx, _video_current_key
    global _video_fade_frames, _video_fade_idx, _video_loading
    if _video_loading:
        return
    n = now_kst()
    is_rainy = weather_emoji in ("weather_rain", "weather_thunderstorm")
    fname, key = _get_video_slot(n.hour, is_rainy)
    if key == _video_current_key:
        return
    _log(f"[Video] 전환: {_video_current_key} → {key}")
    _video_loading = True

    # fade-out 색상 결정
    cur_night = _is_night(n.hour)
    new_fname_night = _is_night(n.hour)  # 새 시간대도 체크
    if cur_night:
        fadeout_color = (0, 0, 0)
    else:
        fadeout_color = (255, 255, 255)

    # fade-out 프레임 생성
    fade_out, _ = _generate_fade_frames(fadeout_color, 12)

    # 백그라운드에서 새 영상 로드
    def _bg_load():
        global _video_frames, _video_idx, _video_current_key
        global _video_fade_frames, _video_fade_idx, _video_loading
        new_frames = _load_video_file(fname)
        if not new_frames:
            _video_loading = False
            return
        # fade-in 색상: 새 시간대 기준
        if _is_night(n.hour):
            fadein_color = (0, 0, 0)
        else:
            fadein_color = (255, 255, 255)
        fade_in = _generate_fadein_frames(new_frames, fadein_color, 12)
        _video_fade_frames = fade_out + fade_in
        _video_fade_idx = 0
        # 로드 완료 후 교체
        _video_frames = new_frames
        _video_idx = 0
        _video_current_key = key
        _video_loading = False
        _log(f"[Video] 전환 완료: {key}")

    threading.Thread(target=_bg_load, daemon=True).start()

def _next_video_frame():
    """다음 영상 프레임 반환 (페이드 전환 포함). 없으면 None."""
    global _video_idx, _video_fade_idx, _video_fade_frames
    # 페이드 전환 중이면 페이드 프레임 반환
    if _video_fade_frames and _video_fade_idx < len(_video_fade_frames):
        frame = _video_fade_frames[_video_fade_idx]
        _video_fade_idx += 1
        if _video_fade_idx >= len(_video_fade_frames):
            _video_fade_frames = []
            _video_fade_idx = 0
        return frame
    if not _video_frames:
        return None
    frame = _video_frames[_video_idx]
    _video_idx = (_video_idx + 1) % len(_video_frames)
    return frame

def _draw_header_bg(img, d, weather_emoji):
    """날씨 기반 동적 헤더 배경."""
    if weather_emoji in ("weather_rain", "weather_thunderstorm"):
        # 비 — 어두운 블루그레이 + 빗줄기 + 블러 (유리 빗물 효과)
        for y in range(0, HEADER_H, 2):
            t = y / HEADER_H
            c = (int(35 + 25*t), int(45 + 25*t), int(65 + 25*t))
            d.rectangle([(0, y), (SCREEN_WIDTH, y+2)], fill=c)
        for i in range(35):
            x = (i * 37 + 13) % SCREEN_WIDTH
            y = (i * 53 + 7) % HEADER_H
            length = 15 + (i * 23) % 35
            d.line([(x, y), (x - 4, y + length)], fill=(150, 175, 200, 100), width=1)
        hdr = img.crop((0, 0, SCREEN_WIDTH, HEADER_H))
        hdr = hdr.filter(ImageFilter.GaussianBlur(3))
        img.paste(hdr, (0, 0))
    elif weather_emoji == "weather_snow":
        for y in range(0, HEADER_H, 2):
            t = y / HEADER_H
            c = (int(60 + 55*t), int(70 + 55*t), int(95 + 50*t))
            d.rectangle([(0, y), (SCREEN_WIDTH, y+2)], fill=c)
    elif weather_emoji == "weather_clear":
        # 맑음 — 청명한 파란 하늘
        for y in range(0, HEADER_H, 2):
            t = y / HEADER_H
            c = (int(25 + 60*t), int(100 + 70*t), int(210 + 30*t))
            d.rectangle([(0, y), (SCREEN_WIDTH, y+2)], fill=c)
    else:
        # 흐림 / 기본 — 회색 그라디언트
        for y in range(0, HEADER_H, 2):
            t = y / HEADER_H
            c = (int(75 + 55*t), int(80 + 55*t), int(100 + 50*t))
            d.rectangle([(0, y), (SCREEN_WIDTH, y+2)], fill=c)


def _clean_title(text):
    """Remove emoji (SMP + misc symbols) that Pretendard can't render."""
    out = []
    for c in text:
        cp = ord(c)
        if cp >= 0x10000:
            continue
        if 0x2600 <= cp <= 0x27BF:
            continue
        if cp == 0xFE0F or cp == 0x200D:
            continue
        out.append(c)
    return ''.join(out).strip()


def _fit_text(text, f, max_w):
    """Fit text in pixel width, add … only when it truly overflows."""
    if f.getlength(text) <= max_w:
        return text
    ew = f.getlength("…")
    for i in range(len(text), 0, -1):
        if f.getlength(text[:i]) + ew <= max_w:
            return text[:i] + "…"
    return "…"


def _wrap_text(text, f, max_w, max_lines=2):
    """Wrap text into lines. Last line truncated with … if needed."""
    if f.getlength(text) <= max_w:
        return [text]
    lines = []
    remaining = text
    while remaining and len(lines) < max_lines:
        if f.getlength(remaining) <= max_w:
            lines.append(remaining)
            remaining = ""
            break
        if len(lines) == max_lines - 1:
            lines.append(_fit_text(remaining, f, max_w))
            remaining = ""
            break
        cut = len(remaining)
        while cut > 1 and f.getlength(remaining[:cut]) > max_w:
            cut -= 1
        lines.append(remaining[:cut])
        remaining = remaining[cut:]
    return lines or [text[:1]]


def _draw_header_text(img, d, weather, air):
    """헤더 텍스트 렌더 (날짜, 시간, 날씨, 미세먼지)."""
    n = now_kst()
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    d.text((30, 28),
           f"{n.year}년 {n.month}월 {n.day}일 ({weekdays[n.weekday()]})",
           font=font(45, 700), fill=C_WHITE)

    hr = n.hour
    ap = "PM" if hr >= 12 else "AM"
    dh = hr % 12 or 12
    # 날짜 폭 기준 양끝 정렬: PM 왼쪽=30, 시간숫자 오른쪽=30+date_w
    date_text = f"{n.year}년 {n.month}월 {n.day}일 ({weekdays[n.weekday()]})"
    date_w = font(45, 700).getlength(date_text)
    right_edge = 30 + int(date_w)
    tf = font(98, 700)
    time_str = f"{dh}:{n.strftime('%M')}"
    time_w = tf.getlength(time_str)
    d.text((30, 72), ap, font=tf, fill=C_WHITE)
    d.text((right_edge - int(time_w), 72), time_str, font=tf, fill=C_WHITE)

    wy = WEATHER_Y
    wx = 30
    if weather:
        we = emoji(weather["emoji"], 63)
        img.paste(we, (wx, wy), we.split()[3] if we.mode == "RGBA" else None)
        d = ImageDraw.Draw(img)
        wt = f"{weather['text']} / {weather['temp']:.0f}°" if isinstance(weather['temp'], (int, float)) else f"{weather['text']} / {weather['temp']}°"
        d.text((wx + 80, wy + 18), wt, font=font(28, 600), fill=C_WHITE)
        wx = wx + 80 + int(font(28, 600).getlength(wt)) + 18
        # 구분선 | (이모지 높이만큼)
        d.line([(wx, wy), (wx, wy + 57)], fill=(255, 255, 255, 140), width=1)
        wx += 15 + 15  # |라인 뒤 15px + 이모지 앞 15px

    if air:
        ae = emoji(air["grade_emoji"], 57)
        img.paste(ae, (wx, wy + 3), ae.split()[3] if ae.mode == "RGBA" else None)
        d = ImageDraw.Draw(img)
        gc = GRADE_COLOR.get(air["grade"], (80, 200, 100))
        badge_text = f"{air['pm10']} ({air['grade_text']})"
        bf = font(22, 600)
        tw = bf.getlength(badge_text)
        bx = wx + 62 + 15  # 이모지↔뱃지 15px 간격
        d.rounded_rectangle(
            [(bx, wy + 18), (bx + tw + 20, wy + 50)],
            radius=16, fill=gc)
        d.text((bx + 10, wy + 20), badge_text, font=bf, fill=C_WHITE)


def render_header(weather, air):
    """헤더만 빠르게 렌더 (영상 배경 + 텍스트). 480×320 반환."""
    vf = _next_video_frame()
    if vf:
        img = vf.copy()
    else:
        img = Image.new("RGB", (SCREEN_WIDTH, HEADER_H), (50, 100, 200))
        d = ImageDraw.Draw(img)
        w_emoji = weather["emoji"] if weather else None
        _draw_header_bg(img, d, w_emoji)
    # 하단 검은색 그라데이션 오버레이 (날씨/미세먼지 가독성)
    if _header_gradient:
        img = img.convert("RGBA")
        img = Image.alpha_composite(img, _header_gradient)
        img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    _draw_header_text(img, d, weather, air)
    return img


def render(weather, air, allday, timed, toasts, toast_off):
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), C_BG)
    d = ImageDraw.Draw(img)
    n = now_kst()

    # ── Zone A: Header (정적 배경 — 영상 모드에선 render_header 사용) ──
    w_emoji = weather["emoji"] if weather else None
    _draw_header_bg(img, d, w_emoji)
    d = ImageDraw.Draw(img)
    _draw_header_text(img, d, weather, air)

    # ── Zone C: All-Day Events (폰트 24, 픽셀 기반 truncation) ───────
    yp = HEADER_H + 8
    ad_font = font(24, 600)
    ad_max_w = SCREEN_WIDTH - 10 - 20 - 8  # 좌우 패딩 제외

    for i, ev in enumerate(allday[:ALLDAY_MAX]):
        c = hex_rgb(ev["color"])
        title = _fit_text(_clean_title(ev["summary"]), ad_font, ad_max_w)
        d.rounded_rectangle(
            [(10, yp), (SCREEN_WIDTH - 10, yp + ALLDAY_ITEM_H - 3)],
            radius=6, fill=c)
        d.text((20, yp + 8), title, font=ad_font, fill=C_WHITE)
        yp += ALLDAY_ITEM_H

    if len(allday) > ALLDAY_MAX:
        d.text((18, yp + 2), f"+ {len(allday) - ALLDAY_MAX}개 더",
               font=font(20), fill=C_GRAY_TEXT)
        yp += 32

    if allday:
        yp += 6

    # ── Zone D: Timeline ─────────────────────────────────────────────
    tl_top = yp + 7  # 종일 일정과 타임라인 사이 패딩
    tl_bot = SCREEN_HEIGHT - 15
    total_h = HOUR_END - HOUR_START
    hh = (tl_bot - tl_top) / total_h
    EVT_GAP = 4
    EVT_BORDER_W = 1
    EVT_TITLE_FONT = font(32, 600)
    EVT_TIME_FONT = font(18)
    HOUR_LABEL_FONT = font(30, 700)  # Pretendard Bold
    HOUR_LABEL_W = 50                # 시간 레이블 영역 폭
    EVT_LEFT = HOUR_LABEL_W + 8     # 이벤트 시작 x
    EVT_RIGHT = SCREEN_WIDTH - 10    # 이벤트 끝 x
    EVT_FULL_W = EVT_RIGHT - EVT_LEFT

    # Hour grid — 큰 숫자 (8~24)
    for h in range(HOUR_START, HOUR_END + 1):
        hy = tl_top + (h - HOUR_START) * hh
        lb = str(h)
        d.text((8, hy - 16), lb, font=HOUR_LABEL_FONT, fill=C_HOUR)
        d.line([(EVT_LEFT - 4, hy), (SCREEN_WIDTH - 8, hy)], fill=C_LINE, width=1)

    # ── 겹침 감지 + 컬럼 배정 ──
    ev_rects = []  # (sh, eh, ev, y1, y2, col)
    for ev in timed:
        sh = ev["start"].hour + ev["start"].minute / 60
        eh = ev["end"].hour + ev["end"].minute / 60
        if eh <= sh:
            eh = 24
        if eh <= HOUR_START or sh >= HOUR_END:
            continue
        sh = max(sh, HOUR_START)
        eh = min(eh, HOUR_END)
        y1 = tl_top + (sh - HOUR_START) * hh + EVT_GAP
        y2 = tl_top + (eh - HOUR_START) * hh - EVT_GAP
        if eh >= HOUR_END:
            y2 += 7  # 24시 끝 이벤트 하단 확장
        if y2 - y1 < 80:
            y2 = y1 + 80
        # 컬럼 배정: 이전 이벤트와 겹치면 col 증가
        col = 0
        for _, prev_eh, _, _, _, prev_col in ev_rects:
            if sh < prev_eh and eh > sh:
                # 시간 겹침 → 같은 col이면 다음 col
                if prev_col >= col:
                    col = prev_col + 1
        ev_rects.append((sh, eh, ev, y1, y2, col))

    # Timed events — 컬럼별 폭 축소
    for sh, eh, ev, y1, y2, col in ev_rects:
        shrink = 0.15 * col  # col0=100%, col1=85%, col2=70%...
        evt_w = int(EVT_FULL_W * (1 - shrink))
        ex1 = EVT_RIGHT - evt_w   # 오른쪽 정렬
        ex2 = EVT_RIGHT
        bar_x2 = ex1 + 8          # 좌측 컬러바

        c = hex_rgb(ev["color"])
        is_past = ev["end"] <= n
        if is_past:
            c = _desaturate(c)
        lc = tuple(min(255, int(v * 0.35 + 255 * 0.65)) for v in c)
        border_c = tuple(min(255, int(v * 0.6 + 100 * 0.4)) for v in c)
        title_fill = C_DIM if is_past else C_DARK_TEXT
        time_fill = C_LIGHT if is_past else C_GRAY_TEXT

        d.rounded_rectangle(
            [(ex1, y1), (ex2, y2)],
            radius=10, fill=lc, outline=border_c, width=EVT_BORDER_W)
        d.rounded_rectangle([(ex1, y1), (bar_x2, y2)], radius=4, fill=c)

        text_x = ex1 + 17
        text_w = ex2 - text_x - 6
        title = _clean_title(ev["summary"])
        block_h = y2 - y1
        time_h = 24
        title_area = block_h - time_h - 12
        line_h = 37
        max_lines = max(1, int(title_area / line_h))
        lines = _wrap_text(title, EVT_TITLE_FONT, text_w, max_lines)

        for li, line in enumerate(lines):
            d.text((text_x, y1 + 6 + li * line_h), line,
                   font=EVT_TITLE_FONT, fill=title_fill)

        d.text((text_x, y2 - 28), ev["time_str"],
               font=EVT_TIME_FONT, fill=time_fill)

    # NOW line — 레드 글래스모피즘
    nh = n.hour + n.minute / 60
    if HOUR_START <= nh <= HOUR_END:
        ny = tl_top + (nh - HOUR_START) * hh
        bar_h = 10
        bar_y1 = int(ny - bar_h / 2)
        bar_y2 = bar_y1 + bar_h
        tri_w = 25

        img = img.convert("RGBA")
        # 배경 블러
        bar_region = img.crop((0, max(0, bar_y1), SCREEN_WIDTH, min(SCREEN_HEIGHT, bar_y2)))
        blurred = bar_region.filter(ImageFilter.GaussianBlur(12))
        img.paste(blurred, (0, max(0, bar_y1)))

        now_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        nd = ImageDraw.Draw(now_overlay)
        # 반투명 레드 바 (x=0부터, 삼각형과 겹침, 20% 더 투명)
        nd.rectangle([(0, bar_y1), (SCREEN_WIDTH - 5, bar_y2)],
                      fill=(220, 40, 40, 112))
        # 좌측 삼각형 ▶ (바 위에 겹침, 25px)
        tri_cy = int(ny)
        tri_h = 13
        nd.polygon([(0, tri_cy - tri_h), (tri_w, tri_cy), (0, tri_cy + tri_h)],
                    fill=(220, 40, 40, 180))
        img = Image.alpha_composite(img, now_overlay)
        img = img.convert("RGB")
        d = ImageDraw.Draw(img)

    # ── Toast: 글래스모피즘 ─────────────────────────────────────────
    if toasts:
        img = img.convert("RGBA")
        TX1, TX2 = 28, SCREEN_WIDTH - 28   # 좌우 28px 마진 (10px 확장)
        TRAD = 28                           # 라운드 반경

        for i, em in enumerate(toasts):
            ty = SCREEN_HEIGHT - TOAST_MARGIN - (i + 1) * (TOAST_H + TOAST_PAD) + toast_off
            if ty > SCREEN_HEIGHT or ty + TOAST_H < 0:
                continue

            # 배경 블러 — 라운드 마스크로 클리핑하여 모서리 아티팩트 제거
            cx1, cy1 = TX1, max(0, ty)
            cx2, cy2 = TX2, min(SCREEN_HEIGHT, ty + TOAST_H)
            if cy2 > cy1:
                region = img.crop((cx1, cy1, cx2, cy2))
                blurred = region.filter(ImageFilter.GaussianBlur(30))
                # 라운드 마스크 생성
                mask = Image.new("L", (cx2 - cx1, cy2 - cy1), 0)
                md = ImageDraw.Draw(mask)
                ry1 = max(0, ty) - ty
                ry2 = min(SCREEN_HEIGHT, ty + TOAST_H) - ty
                md.rounded_rectangle(
                    [(0, ry1), (cx2 - cx1, ry2)],
                    radius=TRAD, fill=255)
                img.paste(blurred, (cx1, cy1), mask)

            overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rounded_rectangle(
                [(TX1, ty), (TX2, ty + TOAST_H)],
                radius=TRAD, fill=(0, 0, 0, 25))
            od.rounded_rectangle(
                [(TX1, ty), (TX2, ty + TOAST_H)],
                radius=TRAD, fill=(255, 255, 255, 40))
            od.rounded_rectangle(
                [(TX1, ty), (TX2, ty + TOAST_H)],
                radius=TRAD, outline=(0, 0, 0, 40), width=2)
            od.rounded_rectangle(
                [(TX1 + 2, ty + 2), (TX2 - 2, ty + TOAST_H - 2)],
                radius=TRAD - 2, outline=(255, 255, 255, 120), width=1)
            img = Image.alpha_composite(img, overlay)

            d = ImageDraw.Draw(img)

            # 아이콘 (이름과 내용 동일 왼쪽 정렬)
            ei = emoji("email", 30)
            img.paste(ei, (TX1 + 10, ty + 12), ei.split()[3] if ei.mode == "RGBA" else None)

            # 이름: 아이콘 옆 (28pt)
            sender = _fit_text(em["sender"], font(28, 600), TX2 - TX1 - 150)
            d.text((TX1 + 44, ty + 10), sender, font=font(28, 600), fill=C_DARK_TEXT)

            # 시간: 우측 정렬 (23pt, 텍스트 너비 기반)
            age = time.time() - em["timestamp"]
            if age < 60:
                at = "방금"
            elif age < 3600:
                at = f"{int(age/60)}분 전"
            else:
                at = f"{int(age/3600)}시간 전"
            tf = font(23)
            tw = tf.getlength(at) if hasattr(tf, "getlength") else d.textlength(at, font=tf)
            d.text((TX2 - 10 - int(tw), ty + 14), at, font=tf, fill=C_GRAY_TEXT)

            # 제목: 아이콘과 동일 x 정렬 (25pt, 축소)
            subj = _fit_text(
                em.get("subject", em.get("snippet", "")),
                font(25, 500), TX2 - TX1 - 30)
            d.text((TX1 + 10, ty + 52), subj, font=font(25, 500), fill=C_GRAY_TEXT)

        img = img.convert("RGB")

    return img


def render_toast_partial(body_cache, toasts, toast_off):
    """body_cache(480x1600)의 하단에 토스트 오버레이 → partial 이미지(480xTOAST_AREA_H) 반환."""
    if not toasts or body_cache is None:
        return None

    body_h = SCREEN_HEIGHT - HEADER_H
    crop_y = body_h - TOAST_AREA_H
    TX1, TX2 = 28, SCREEN_WIDTH - 28
    TRAD = 28

    img = body_cache.crop((0, crop_y, SCREEN_WIDTH, body_h)).copy()
    img = img.convert("RGBA")

    for i, em in enumerate(toasts):
        ty_screen = SCREEN_HEIGHT - TOAST_MARGIN - (i + 1) * (TOAST_H + TOAST_PAD) + toast_off
        ty = ty_screen - HEADER_H - crop_y

        if ty > TOAST_AREA_H or ty + TOAST_H < 0:
            continue

        cx1, cy1 = TX1, max(0, ty)
        cx2, cy2 = TX2, min(TOAST_AREA_H, ty + TOAST_H)
        if cy2 > cy1:
            region = img.crop((cx1, cy1, cx2, cy2))
            blurred = region.filter(ImageFilter.GaussianBlur(30))
            mask = Image.new("L", (cx2 - cx1, cy2 - cy1), 0)
            md = ImageDraw.Draw(mask)
            ry1 = max(0, ty) - ty
            ry2 = min(TOAST_AREA_H, ty + TOAST_H) - ty
            md.rounded_rectangle(
                [(0, ry1), (cx2 - cx1, ry2)],
                radius=TRAD, fill=255)
            img.paste(blurred, (cx1, cy1), mask)

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [(TX1, ty), (TX2, ty + TOAST_H)],
            radius=TRAD, fill=(0, 0, 0, 25))
        od.rounded_rectangle(
            [(TX1, ty), (TX2, ty + TOAST_H)],
            radius=TRAD, fill=(255, 255, 255, 40))
        od.rounded_rectangle(
            [(TX1, ty), (TX2, ty + TOAST_H)],
            radius=TRAD, outline=(0, 0, 0, 40), width=2)
        od.rounded_rectangle(
            [(TX1 + 2, ty + 2), (TX2 - 2, ty + TOAST_H - 2)],
            radius=TRAD - 2, outline=(255, 255, 255, 120), width=1)
        img = Image.alpha_composite(img, overlay)

        d = ImageDraw.Draw(img)

        ei = emoji("email", 30)
        img.paste(ei, (TX1 + 10, ty + 12), ei.split()[3] if ei.mode == "RGBA" else None)

        sender = _fit_text(em["sender"], font(28, 600), TX2 - TX1 - 150)
        d.text((TX1 + 44, ty + 10), sender, font=font(28, 600), fill=C_DARK_TEXT)

        age = time.time() - em["timestamp"]
        if age < 60:
            at = "방금"
        elif age < 3600:
            at = f"{int(age/60)}분 전"
        else:
            at = f"{int(age/3600)}시간 전"
        tf = font(23)
        tw = tf.getlength(at) if hasattr(tf, "getlength") else d.textlength(at, font=tf)
        d.text((TX2 - 10 - int(tw), ty + 14), at, font=tf, fill=C_GRAY_TEXT)

        subj = _fit_text(
            em.get("subject", em.get("snippet", "")),
            font(25, 500), TX2 - TX1 - 30)
        d.text((TX1 + 10, ty + 52), subj, font=font(25, 500), fill=C_GRAY_TEXT)

    return img.convert("RGB")


# ─── System Tray ──────────────────────────────────────────────────────────

_paused = False
_force = False
_exit = False

def _on_refresh(icon, item):
    global _force
    _force = True

def _on_pause(icon, item):
    global _paused
    _paused = not _paused
    icon.update_menu()

def _on_exit(icon, item):
    global _exit
    _exit = True
    icon.stop()

def start_tray():
    if not HAS_PYSTRAY:
        _log("[Tray] pystray not available")
        return None
    try:
        ico = _res("ico/growny_app_icon.ico")
        try:
            im = Image.open(ico).convert("RGBA")
        except Exception:
            im = Image.new("RGBA", (64, 64), (30, 144, 255, 255))
        menu = Menu(
            MenuItem("로그 확인", _open_log_viewer),
            Menu.SEPARATOR,
            MenuItem("즉시 갱신", _on_refresh),
            MenuItem(lambda item: "재시작" if _paused else "일시 중지", _on_pause),
            Menu.SEPARATOR,
            MenuItem("Windows 시작 시 자동실행",
                     _on_toggle_startup,
                     checked=lambda item: is_startup_registered()),
            Menu.SEPARATOR,
            MenuItem("Exit", _on_exit),
        )
        icon = pystray.Icon("growny_mindblower", im, "Growny Mindblower", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        _log("[Tray] System tray icon started")
        return icon
    except Exception as e:
        _log(f"[Tray] Failed to start: {e}")
        return None

# ─── Windows Startup ──────────────────────────────────────────────────────

_STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_REG_NAME = "GrownyMindblower"

def is_startup_registered():
    if os.name != "nt":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _STARTUP_REG_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def register_startup():
    if os.name != "nt":
        return
    try:
        import winreg
        if getattr(sys, "frozen", False):
            exe = sys.executable
        else:
            _log("[Startup] Skipped — EXE가 아닌 환경에서는 등록하지 않습니다")
            return
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, _STARTUP_REG_NAME, 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
        _log("[Startup] Registered")
    except Exception:
        pass

def unregister_startup():
    if os.name != "nt":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _STARTUP_REG_NAME)
        winreg.CloseKey(key)
        _log("[Startup] Unregistered")
    except Exception:
        pass

def _on_toggle_startup(icon, item):
    if is_startup_registered():
        unregister_startup()
    else:
        register_startup()

# ─── Display Init ─────────────────────────────────────────────────────────

def _kill_com_holders():
    """Kill any process holding the COM port (previous instances)."""
    if os.name != "nt":
        return
    import subprocess
    my_pid = os.getpid()
    # Kill other SmartCalendar instances (exclude self)
    for name in ["SmartCalendar.exe", "smart_calendar.exe"]:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 f"Get-Process -Name '{name.replace('.exe','')}' -ErrorAction SilentlyContinue | "
                 f"Where-Object {{$_.Id -ne {my_pid}}} | Stop-Process -Force"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    # Also kill any python process holding COM4 via serial
    try:
        subprocess.run(
            ["powershell", "-Command",
             f"Get-Process python* -ErrorAction SilentlyContinue | "
             f"Where-Object {{$_.Id -ne {my_pid}}} | Stop-Process -Force"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

def _try_reconnect(old_screen):
    """시리얼 에러 시 재연결 시도. 실패하면 None 반환 (headless 전환)."""
    _log("[Display] Serial error — reconnecting in 5s...")
    try:
        old_screen.closeSerial()
    except Exception:
        pass
    time.sleep(5)
    new_screen = open_display(retries=2)
    if new_screen:
        new_screen.SetOrientation(Orientation.PORTRAIT)
        new_screen.SetBrightness(100)
        _log("[Display] Reconnected OK")
        return new_screen
    _log("[Display] Reconnect failed — headless mode until next attempt")
    return None

def open_display(retries=3):
    if not HAS_DISPLAY:
        return None
    # Always kill potential holders on first attempt
    _kill_com_holders()
    time.sleep(2)
    for attempt in range(retries):
        try:
            scr = LcdCommRevC(com_port=COM_PORT, display_width=480, display_height=1920)
            scr.InitializeComm()
            return scr
        except Exception as e:
            _log(f"[Display] Attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                _kill_com_holders()
                time.sleep(3)
    _log(f"[Display] Failed to open {COM_PORT}")
    return None

# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    global _force, _exit

    _log("[Turzx] Smart Calendar v2 booting...")

    tray = start_tray()
    register_startup()

    screen = open_display()
    if screen:
        screen.SetOrientation(Orientation.PORTRAIT)
        screen.SetBrightness(100)
        _log(f"[Turzx] Display OK on {COM_PORT}")
    else:
        _log("[Turzx] No display - headless mode")

    # 영상 프리로드
    _load_video()

    toast = ToastManager()
    weather = {"temp": "--", "text": "로딩중", "emoji": "weather_clear"}
    air = {"pm10": "--", "pm25": "--", "grade": 2, "grade_text": "보통", "grade_emoji": "moderate"}
    allday, timed = [], []
    last_api = 0
    last_email = 0
    last_body = 0       # 캘린더 본문(헤더 아래) 마지막 갱신
    body_image = None   # 캘린더 본문 캐시

    has_video = len(_video_frames) > 0

    _log(f"[Turzx] Running. Display: {DISPLAY_REFRESH_SEC}s / API: {API_REFRESH_SEC}s / Email: {TOAST_CHECK_SEC}s / Video: {has_video}")

    # ── API를 백그라운드 스레드로 분리 (영상 루프 블로킹 방지) ──
    last_weather = 0
    _body_dirty = True          # body 갱신 필요 플래그

    def _api_worker():
        nonlocal weather, air, allday, timed, last_api, last_email, last_weather, _body_dirty
        while not _exit:
            try:
                now = time.time()
                # 날씨/미세먼지: 2분마다
                if now - last_weather >= WEATHER_REFRESH_SEC:
                    w = fetch_weather()
                    if w is not None:
                        weather = w
                    a = fetch_air()
                    if a is not None:
                        air = a
                    last_weather = time.time()
                    _body_dirty = True
                    # 날씨/시간 변경 시 영상 전환 체크
                    w_emoji = weather["emoji"] if weather else "weather_clear"
                    check_video_switch(w_emoji)

                # 캘린더 이벤트: 15초마다
                if now - last_api >= API_REFRESH_SEC or _force:
                    allday, timed = fetch_events()
                    last_api = time.time()
                    _body_dirty = True
                    if tray:
                        tray.title = f"Turzx - Synced {datetime.datetime.now().strftime('%H:%M')}"

                if now - last_email >= TOAST_CHECK_SEC:
                    emails = fetch_emails()
                    _log(f"[Gmail] fetched {len(emails)} unread, toast phase={toast.phase}")
                    toast.update(emails)
                    last_email = time.time()
                    if emails:
                        _body_dirty = True

                time.sleep(2)
            except Exception as e:
                _log(f"[API] {e}")
                time.sleep(10)

    api_thread = threading.Thread(target=_api_worker, daemon=True)
    api_thread.start()

    # 첫 API 호출 완료 대기
    time.sleep(8)

    # ── 공식 프로토콜: 최초 1회 풀스크린 전송으로 디스플레이 초기화 ──
    if screen:
        try:
            init_img = render(weather, air, allday, timed, [], 0)
            if has_video:
                vf = render_header(weather, air)
                init_img.paste(vf, (0, 0))
            screen.DisplayPILImage(init_img)  # 풀스크린 (초기화용, 1회만)
            _log("[Turzx] Initial full-screen sent (protocol init)")
            time.sleep(1)
            # Count.Start 리셋 — 이후 partial이 0부터 시작하도록
            from library.lcd.lcd_comm_rev_c import Count
            Count.Start = 0
        except Exception as e:
            _log(f"[Turzx] Init display: {e}")

    _toast_was_active = False

    while not _exit:
        try:
            if _paused:
                time.sleep(1)
                continue

            if has_video and screen:
                # ── 영상 모드 ──
                t_list, t_off = toast.tick()

                # 1) body: 데이터 변경 or 토스트 애니메이션 시
                need_body = (body_image is None
                             or _body_dirty
                             or toast.animating
                             or (toast.is_active and not _toast_was_active)
                             or (not toast.is_active and _toast_was_active))
                if need_body:
                    full = render(weather, air, allday, timed, t_list, t_off)
                    body_image = full.crop((0, HEADER_H, SCREEN_WIDTH, SCREEN_HEIGHT))
                    try:
                        screen.DisplayPILImage(body_image, x=0, y=HEADER_H)
                        time.sleep(0.3)
                    except Exception as e:
                        _log(f"[Display] body: {e}")
                        screen = _try_reconnect(screen)
                    _body_dirty = False
                    last_body = time.time()

                _toast_was_active = toast.is_active

                # 2) header(320px) 영상 — 매 프레임
                header_img = render_header(weather, air)
                try:
                    screen.DisplayPILImage(header_img, x=0, y=0)
                except Exception as e:
                    _log(f"[Display] header: {e}")
                    screen = _try_reconnect(screen)

            else:
                # ── 정적 모드: partial update로 분할 전송 ──
                t_list, t_off = toast.tick()
                image = render(weather, air, allday, timed, t_list, t_off)
                if screen:
                    try:
                        hdr = image.crop((0, 0, SCREEN_WIDTH, HEADER_H))
                        screen.DisplayPILImage(hdr, x=0, y=0)
                        body = image.crop((0, HEADER_H, SCREEN_WIDTH, SCREEN_HEIGHT))
                        screen.DisplayPILImage(body, x=0, y=HEADER_H)
                    except Exception as e:
                        _log(f"[Display] {e}")
                        screen = _try_reconnect(screen)
                time.sleep(2 if toast.animating else DISPLAY_REFRESH_SEC)

        except KeyboardInterrupt:
            break
        except Exception as e:
            _log(f"[Turzx] {e}")
            time.sleep(5)

    if screen:
        try:
            screen.SetBrightness(0)
        except Exception:
            pass
    _log("[Turzx] Exited.")

def preview():
    """React dev server style - 브라우저에서 실시간 프리뷰."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import io

    port = 8880
    _log(f"[Preview] http://localhost:{port} 에서 미리보기")
    _log("[Preview] 10초마다 자동 새로고침. Ctrl+C로 종료.")

    toast = ToastManager()
    state = {"weather": None, "air": None, "allday": [], "timed": [],
             "last_api": 0, "last_email": 0, "last_weather": 0}

    def refresh_data():
        now = time.time()
        if now - state["last_weather"] >= WEATHER_REFRESH_SEC:
            w = fetch_weather()
            if w is not None:
                state["weather"] = w
            a = fetch_air()
            if a is not None:
                state["air"] = a
            state["last_weather"] = now
        if now - state["last_api"] >= API_REFRESH_SEC:
            state["allday"], state["timed"] = fetch_events()
            state["last_api"] = now
        if now - state["last_email"] >= TOAST_CHECK_SEC:
            emails = fetch_emails()
            toast.update(emails)
            state["last_email"] = now

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass  # 브라우저 연결 끊김 무시
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"""<!DOCTYPE html>
<html><head>
<title>Turzx Preview</title>
<style>
  body {{ margin:0; background:#111; display:flex; justify-content:center; align-items:flex-start; min-height:100vh; padding:20px 0; }}
  img {{ height:90vh; image-rendering:auto; border:2px solid #333; border-radius:8px; }}
  .wrap {{ text-align:center; }}
  h3 {{ color:#888; font-family:sans-serif; margin:8px 0; }}
  .btn {{ background:#333; color:#ccc; border:1px solid #555; padding:6px 16px; border-radius:6px; cursor:pointer; font-size:14px; margin:4px; }}
  .btn:hover {{ background:#555; }}
  .btn.active {{ background:#c44; color:#fff; }}
</style>
</head><body>
<div class="wrap">
  <h3>Turzx 8.8" Preview (480x1920) - Auto-refresh {DISPLAY_REFRESH_SEC}s</h3>
  <div>
    <button class="btn" onclick="toggleMock()" id="mockBtn">Mock Toast</button>
    <button class="btn" onclick="location.reload()">Refresh</button>
  </div>
  <img src="/render.png?t=" id="img">
</div>
<script>
  let mock = false;
  function toggleMock() {{
    mock = !mock;
    document.getElementById('mockBtn').className = mock ? 'btn active' : 'btn';
    update();
  }}
  function update() {{
    const q = mock ? '&mock=1' : '';
    document.getElementById('img').src = '/render.png?t=' + Date.now() + q;
  }}
  setInterval(update, {DISPLAY_REFRESH_SEC * 1000});
</script>
</body></html>""".encode())
            elif self.path.startswith("/render.png"):
                refresh_data()
                # ?mock=1 → 디자인 캡처용 mock toast 강제 표시
                if "mock=1" in self.path:
                    mock_toasts = [
                        {"sender": "김민수", "subject": "회의 일정 변경 안내드립니다",
                         "timestamp": time.time() - 180, "snippet": ""},
                        {"sender": "이지영 팀장", "subject": "주간 보고서 검토 부탁드려요",
                         "timestamp": time.time() - 7200, "snippet": ""},
                    ]
                    t_list, t_off = mock_toasts, 0
                else:
                    t_list, t_off = toast.tick()
                img = render(state["weather"], state["air"],
                             state["allday"], state["timed"], t_list, t_off)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(buf.getvalue())
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, format, *args):
            pass  # suppress request logs

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
    ReusableHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    if "--preview" in sys.argv:
        preview()
    else:
        main()
