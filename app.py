#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
歌詞入力 + 音声ファイル -> LRC / timed.json / EXO を一括生成するGUI

安定版:
- 背景画像/動画のEXO出力は入れない
- TkinterのGUI更新はメインスレッドで実行する
- LRCタイムスタンプの丸め繰り上がりを補正
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import queue
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, colorchooser

try:
    from rapidfuzz import fuzz
except Exception as e:
    fuzz = None
    _rapidfuzz_import_error = e
else:
    _rapidfuzz_import_error = None

try:
    import whisper
except Exception as e:
    whisper = None
    _whisper_import_error = e
else:
    _whisper_import_error = None


MODEL_NAME = "small"
DEFAULT_SEARCH_WINDOW = 18
DEFAULT_THRESHOLD = 45.0

FONT_FALLBACK_CHOICES = [
    "メイリオ",
    "Yu Gothic",
    "Yu Mincho",
    "MS Gothic",
    "MS Mincho",
    "Meiryo UI",
    "Arial",
    "Segoe UI",
]

TEXT_DIRECTION_VALUES = {
    "横書き": "horizontal",
    "縦書き": "vertical",
}

DEFAULT_EXO_CONFIG = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "audio_ch": 2,
    "font": "メイリオ",
    "text_direction": "horizontal",
    "text_align": 4,
    "font_size": 80,
    "color": "ffffff",
    "color2": "000000",
    "border_type": 1,
    "text_x": 0.0,
    "text_y": 200.0,
    "fade_in_frames": 8,
    "fade_out_frames": 8,
    "text_layer": 5,
    "audio_layer": 1,
    "max_display_frames": 0,
    "minimum_score": 0.0,
    "minimum_duration_frames": 2,
}


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class MatchResult:
    lyric: str
    start: float
    end: float
    score: float
    seg_start: int
    seg_end: int


@dataclass
class ExoLine:
    start_ms: int
    end_ms: int
    text: str
    score: float


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[、。,.!?！？…・「」『』（）()\[\]【】\-_~—]+", "", text)
    return text


def sec_to_lrc(sec: float, milliseconds: bool = False) -> str:
    if sec < 0:
        sec = 0.0
    total_ms = int(round(sec * 1000))
    mm = total_ms // 60000
    rest_ms = total_ms % 60000
    ss = rest_ms // 1000
    ms = rest_ms % 1000
    if milliseconds:
        return f"[{mm:02}:{ss:02}.{ms:03}]"
    cs = int(round(ms / 10))
    if cs >= 100:
        ss += 1
        cs = 0
        if ss >= 60:
            mm += 1
            ss = 0
    return f"[{mm:02}:{ss:02}.{cs:02}]"


def ms_to_frames(ms: int, fps: int) -> int:
    return round(ms * fps / 1000)


def encode_text_utf16le(text: str) -> str:
    return text.encode("utf-16-le").hex().ljust(4096, "0")


def format_aviutl_font(font_name: str, text_direction: str) -> str:
    font = font_name.strip().lstrip("@") or DEFAULT_EXO_CONFIG["font"]
    if text_direction == "vertical":
        return f"@{font}"
    return font


def format_aviutl_align(base_align: int, text_direction: str) -> int:
    align = int(base_align)
    if text_direction == "vertical":
        align += 9
    return align


def load_lyrics_from_text(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def transcribe_audio(audio_path: str, model_name: str, log=None) -> List[Segment]:
    if whisper is None:
        raise RuntimeError(f"whisper の読み込みに失敗しました: {_whisper_import_error}")
    if log:
        log("Whisperモデル読み込み中...")
    model = whisper.load_model(model_name)
    if log:
        log("音声を文字起こし中...")
    result = model.transcribe(
        audio_path,
        language="ja",
        verbose=False,
        fp16=False,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    segments: List[Segment] = []
    for seg in result.get("segments", []):
        words = seg.get("words") or []
        if words:
            for w in words:
                word = str(w.get("word", "")).strip()
                if not word:
                    continue
                start = float(w.get("start", seg.get("start", 0.0)))
                end = float(w.get("end", seg.get("end", start + 0.1)))
                if end <= start:
                    end = start + 0.10
                segments.append(Segment(start=start, end=end, text=word))
        else:
            text = str(seg.get("text", "")).strip()
            if text:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", start + 1.0))
                if end <= start:
                    end = start + 0.10
                segments.append(Segment(start=start, end=end, text=text))
    return segments


def score_line_against_segments(lyric: str, segments: List[Segment], seg_start: int, seg_end: int) -> float:
    if fuzz is None:
        raise RuntimeError(f"rapidfuzz の読み込みに失敗しました: {_rapidfuzz_import_error}")
    combined = "".join(seg.text for seg in segments[seg_start: seg_end + 1])
    lyric_n = normalize_text(lyric)
    combined_n = normalize_text(combined)
    if not lyric_n or not combined_n:
        return 0.0
    return (
        fuzz.ratio(lyric_n, combined_n) * 0.45
        + fuzz.partial_ratio(lyric_n, combined_n) * 0.40
        + fuzz.token_sort_ratio(lyric_n, combined_n) * 0.15
    )


def align_lyrics(lyrics: List[str], segments: List[Segment], search_window: int = DEFAULT_SEARCH_WINDOW) -> List[MatchResult]:
    results: List[MatchResult] = []
    if not lyrics or not segments:
        return results
    seg_cursor = 0
    for lyric in lyrics:
        if seg_cursor >= len(segments):
            last_end = results[-1].end if results else 0.0
            results.append(MatchResult(lyric, last_end, last_end + 1.0, 0.0, max(0, len(segments) - 1), max(0, len(segments) - 1)))
            continue
        best_score = -1.0
        best_end = seg_cursor
        max_end = min(len(segments) - 1, seg_cursor + search_window - 1)
        for end in range(seg_cursor, max_end + 1):
            score = score_line_against_segments(lyric, segments, seg_cursor, end)
            if score > best_score:
                best_score = score
                best_end = end
        matched_start = segments[seg_cursor].start
        matched_end = segments[best_end].end
        if matched_end <= matched_start:
            matched_end = matched_start + 0.5
        current_start_idx = seg_cursor
        seg_cursor = best_end + 1
        results.append(MatchResult(lyric, matched_start, matched_end, best_score, current_start_idx, best_end))
    return results


def write_lrc(output_path: str, matches: List[MatchResult], milliseconds: bool = False,
              write_tags: bool = False, title: str | None = None, artist: str | None = None) -> None:
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        if write_tags:
            if title:
                f.write(f"[ti:{title}]\n")
            if artist:
                f.write(f"[ar:{artist}]\n")
            f.write("\n")
        for m in matches:
            f.write(f"{sec_to_lrc(m.start, milliseconds=milliseconds)}{m.lyric}\n")


def write_timed_json(output_path: str, matches: List[MatchResult]) -> None:
    data = [
        {
            "lyric": m.lyric,
            "start": round(m.start, 3),
            "end": round(m.end, 3),
            "duration": round(m.end - m.start, 3),
            "score": round(m.score, 1),
        }
        for m in matches
    ]
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_timed_json(filepath: str) -> List[ExoLine]:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    lyrics: List[ExoLine] = []
    for item in data:
        text = str(item.get("lyric", "")).strip()
        if not text:
            continue
        start_sec = float(item.get("start", 0.0))
        end_sec = float(item.get("end", start_sec + 1.0))
        score = float(item.get("score", 0.0))
        if end_sec <= start_sec:
            end_sec = start_sec + 0.1
        lyrics.append(ExoLine(int(round(start_sec * 1000)), int(round(end_sec * 1000)), text, score))
    lyrics.sort(key=lambda x: x.start_ms)
    return lyrics


def build_exo(lyrics: List[ExoLine], config: dict, audio_path: Optional[str] = None) -> str:
    fps = int(config["fps"])
    fade_in = int(config["fade_in_frames"])
    fade_out = int(config["fade_out_frames"])

    segments = []
    for line in lyrics:
        if line.score < float(config["minimum_score"]):
            continue
        start_ms = line.start_ms
        end_ms = line.end_ms
        if int(config["max_display_frames"]) > 0:
            limit_ms = start_ms + int(config["max_display_frames"]) * 1000 // fps
            end_ms = min(end_ms, limit_ms)
        start_f = max(1, ms_to_frames(start_ms, fps))
        end_f = max(start_f + int(config["minimum_duration_frames"]), ms_to_frames(end_ms, fps) - 1)
        segments.append((start_f, end_f, line.text))

    total_frames = segments[-1][1] + fps * 2 if segments else fps * 30
    out: List[str] = [
        "[exedit]",
        f"width={config['width']}",
        f"height={config['height']}",
        f"rate={fps}",
        "scale=1",
        f"length={total_frames}",
        f"audio_ch={config['audio_ch']}",
        "",
    ]

    obj_idx = 0
    if audio_path:
        abs_audio = os.path.abspath(audio_path)
        out += [
            f"[{obj_idx}]",
            "start=1",
            f"end={total_frames}",
            f"layer={config['audio_layer']}",
            "group=1",
            "overlay=1",
            "audio=1",
            f"[{obj_idx}.0]",
            "_name=音声ファイル",
            "再生位置=0.00",
            "再生速度=100.0",
            "ループ再生=0",
            "動画ファイルと連携=0",
            f"file={abs_audio}",
            f"[{obj_idx}.1]",
            "_name=標準再生",
            "音量=100.0",
            "左右=0.0",
            "",
        ]
        obj_idx += 1

    for start_f, end_f, text in segments:
        sub_idx = 0
        out += [
            f"[{obj_idx}]",
            f"start={start_f}",
            f"end={end_f}",
            f"layer={config['text_layer']}",
            "overlay=1",
            "camera=0",
            f"[{obj_idx}.{sub_idx}]",
            "_name=テキスト",
            f"サイズ={config['font_size']}",
            "表示速度=0.0",
            "文字毎に個別オブジェクト=0",
            "移動座標上に表示する=0",
            "自動スクロール=0",
            "B=0",
            "I=0",
            f"type={config['border_type']}",
            "autoadjust=0",
            "soft=0",
            "monospace=0",
            f"align={format_aviutl_align(config['text_align'], config['text_direction'])}",
            "spacing_x=0",
            "spacing_y=0",
            "precision=0",
            f"color={config['color']}",
            f"color2={config['color2']}",
            f"font={format_aviutl_font(config['font'], config['text_direction'])}",
            f"text={encode_text_utf16le(text)}",
        ]
        sub_idx += 1
        out += [
            f"[{obj_idx}.{sub_idx}]",
            "_name=標準描画",
            f"X={config['text_x']:.1f}",
            f"Y={config['text_y']:.1f}",
            "Z=0.0",
            "拡大率=100.00",
            "透明度=0.00",
            "回転=0.00",
            "blend=0",
        ]
        sub_idx += 1
        if fade_in > 0 or fade_out > 0:
            out += [f"[{obj_idx}.{sub_idx}]", "_name=フェード", f"イン={fade_in}", f"アウト={fade_out}"]
        out.append("")
        obj_idx += 1

    return "\r\n".join(out)


def write_exo(output_path: str, exo_content: str) -> None:
    with open(output_path, "w", encoding="shift-jis", errors="replace", newline="") as f:
        f.write(exo_content)


def generate_all(lyrics_text: str, audio_path: str, out_dir: str, model_name: str,
                 search_window: int, milliseconds: bool, write_tags: bool,
                 title: str | None, artist: str | None, exo_config: dict, log=None) -> dict:
    def emit(msg: str) -> None:
        if log:
            log(msg)

    emit("歌詞を読み込み中...")
    lyrics = load_lyrics_from_text(lyrics_text)
    if not lyrics:
        raise RuntimeError("歌詞入力に有効な行が見つかりませんでした。")
    emit(f"歌詞行数: {len(lyrics)}")

    segments = transcribe_audio(audio_path, model_name, log=emit)
    emit(f"Whisperセグメント数: {len(segments)}")

    matches = align_lyrics(lyrics, segments, search_window=search_window)
    matched_count = sum(1 for m in matches if m.score >= DEFAULT_THRESHOLD)
    emit(f"マッチング完了: {matched_count}/{len(matches)}")

    base = Path(audio_path).stem
    os.makedirs(out_dir, exist_ok=True)
    lrc_path = os.path.join(out_dir, base + ".lrc")
    json_path = os.path.join(out_dir, base + ".timed.json")
    exo_path = os.path.join(out_dir, base + ".exo")

    emit("LRCを書き出し中...")
    write_lrc(lrc_path, matches, milliseconds=milliseconds, write_tags=write_tags, title=title, artist=artist)
    emit("JSONを書き出し中...")
    write_timed_json(json_path, matches)
    emit("EXOを書き出し中...")
    exo_content = build_exo(load_timed_json(json_path), exo_config, audio_path=audio_path)
    write_exo(exo_path, exo_content)

    return {"lrc": lrc_path, "json": json_path, "exo": exo_path, "matched": matched_count, "total": len(matches)}


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self._canvas)
        self._win_id = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<Enter>", self._bind_mousewheel)
        self._canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfig(self._win_id, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel_win)
        self._canvas.bind_all("<Button-4>", self._on_mousewheel_up)
        self._canvas.bind_all("<Button-5>", self._on_mousewheel_down)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self._canvas.unbind_all("<MouseWheel>")
        self._canvas.unbind_all("<Button-4>")
        self._canvas.unbind_all("<Button-5>")

    def _on_mousewheel_win(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_up(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(-1, "units")

    def _on_mousewheel_down(self, _event: tk.Event) -> None:
        self._canvas.yview_scroll(1, "units")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("歌詞自動生成ツール")
        self.geometry("1080x940")
        self.minsize(980, 400)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False

        self.audio_var = tk.StringVar()
        self.out_dir_var = tk.StringVar()
        self.model_var = tk.StringVar(value=MODEL_NAME)
        self.window_var = tk.IntVar(value=DEFAULT_SEARCH_WINDOW)
        self.ms_var = tk.BooleanVar(value=False)
        self.tags_var = tk.BooleanVar(value=False)
        self.title_var = tk.StringVar()
        self.artist_var = tk.StringVar()
        self.lyrics_text: tk.Text | None = None
        self.preview_canvas: tk.Canvas | None = None

        self.exo_width_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["width"])
        self.exo_height_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["height"])
        self.exo_fps_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["fps"])
        self.exo_font_var = tk.StringVar(value=DEFAULT_EXO_CONFIG["font"])
        self.exo_direction_var = tk.StringVar(value="横書き")
        self.exo_font_size_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["font_size"])
        self.exo_color_var = tk.StringVar(value=DEFAULT_EXO_CONFIG["color"])
        self.exo_color2_var = tk.StringVar(value=DEFAULT_EXO_CONFIG["color2"])
        self.exo_border_type_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["border_type"])
        self.exo_text_x_var = tk.DoubleVar(value=DEFAULT_EXO_CONFIG["text_x"])
        self.exo_text_y_var = tk.DoubleVar(value=DEFAULT_EXO_CONFIG["text_y"])
        self.exo_fade_in_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["fade_in_frames"])
        self.exo_fade_out_var = tk.IntVar(value=DEFAULT_EXO_CONFIG["fade_out_frames"])

        self._build_ui()
        self._bind_preview_updates()
        self.after(100, self._poll_log_queue)

    def _font_choices(self) -> list[str]:
        try:
            fonts = sorted(set(tkfont.families()))
        except tk.TclError:
            fonts = []
        choices = []
        for font in FONT_FALLBACK_CHOICES + fonts:
            name = font.lstrip("@")
            if name and name not in choices:
                choices.append(name)
        return choices

    def _build_ui(self) -> None:
        scroller = ScrollableFrame(self)
        scroller.pack(fill="both", expand=True)
        root = ttk.Frame(scroller.inner, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="歌詞入力 + 音声 -> LRC / JSON / EXO",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 12))

        input_frame = ttk.LabelFrame(root, text="入力", padding=12)
        input_frame.pack(fill="x", pady=(0, 10))
        lyrics_frame = ttk.Frame(input_frame)
        lyrics_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(lyrics_frame, text="歌詞入力", width=16).pack(side="left", anchor="n")
        lyrics_box_frame = ttk.Frame(lyrics_frame)
        lyrics_box_frame.pack(side="left", fill="x", expand=True)
        self.lyrics_text = tk.Text(lyrics_box_frame, height=8, wrap="word")
        self.lyrics_text.pack(side="top", fill="x", expand=True)
        lyrics_btns = ttk.Frame(lyrics_box_frame)
        lyrics_btns.pack(side="top", fill="x", pady=(6, 0))
        ttk.Button(lyrics_btns, text="TXTから読み込む", command=self._load_lyrics_from_file).pack(side="left")
        ttk.Button(lyrics_btns, text="クリア", command=self.clear_lyrics).pack(side="left", padx=8)
        self._file_row(input_frame, "音声ファイル", self.audio_var, self._browse_audio)
        self._file_row(input_frame, "出力フォルダ", self.out_dir_var, self._browse_outdir)

        settings = ttk.LabelFrame(root, text="変換設定", padding=12)
        settings.pack(fill="x", pady=(0, 10))
        row1 = ttk.Frame(settings)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="Whisperモデル", width=16).pack(side="left")
        ttk.Combobox(row1, textvariable=self.model_var,
                     values=["tiny", "base", "small", "medium", "large"],
                     width=14, state="readonly").pack(side="left")
        ttk.Label(row1, text="探索ウィンドウ", width=16).pack(side="left", padx=(18, 0))
        ttk.Spinbox(row1, from_=1, to=200, textvariable=self.window_var, width=8).pack(side="left")
        ttk.Checkbutton(row1, text="LRCをミリ秒表記", variable=self.ms_var).pack(side="left", padx=(18, 0))
        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="タイトル", width=16).pack(side="left")
        ttk.Entry(row2, textvariable=self.title_var, width=28).pack(side="left")
        ttk.Label(row2, text="アーティスト", width=16).pack(side="left", padx=(18, 0))
        ttk.Entry(row2, textvariable=self.artist_var, width=28).pack(side="left")
        ttk.Checkbutton(row2, text="LRCにタグを書き出す", variable=self.tags_var).pack(side="left", padx=(18, 0))

        exo_frame = ttk.LabelFrame(root, text="EXO表示設定", padding=12)
        exo_frame.pack(fill="x", pady=(0, 10))
        exo_row1 = ttk.Frame(exo_frame)
        exo_row1.pack(fill="x", pady=3)
        ttk.Label(exo_row1, text="動画サイズ", width=16).pack(side="left")
        ttk.Spinbox(exo_row1, from_=320, to=7680, textvariable=self.exo_width_var, width=8).pack(side="left")
        ttk.Label(exo_row1, text="x").pack(side="left", padx=4)
        ttk.Spinbox(exo_row1, from_=240, to=4320, textvariable=self.exo_height_var, width=8).pack(side="left")
        ttk.Label(exo_row1, text="FPS", width=6).pack(side="left", padx=(18, 0))
        ttk.Spinbox(exo_row1, from_=1, to=240, textvariable=self.exo_fps_var, width=8).pack(side="left")
        ttk.Label(exo_row1, text="フォント", width=8).pack(side="left", padx=(18, 0))
        ttk.Combobox(exo_row1, textvariable=self.exo_font_var,
                     values=self._font_choices(), width=18).pack(side="left")
        exo_row2 = ttk.Frame(exo_frame)
        exo_row2.pack(fill="x", pady=3)
        ttk.Label(exo_row2, text="文字サイズ", width=16).pack(side="left")
        ttk.Spinbox(exo_row2, from_=1, to=400, textvariable=self.exo_font_size_var, width=8).pack(side="left")
        ttk.Label(exo_row2, text="縁取り種別", width=10).pack(side="left", padx=(18, 0))
        ttk.Spinbox(exo_row2, from_=0, to=3, textvariable=self.exo_border_type_var, width=6).pack(side="left")
        ttk.Label(exo_row2, text="書字方向", width=10).pack(side="left", padx=(18, 0))
        ttk.Combobox(exo_row2, textvariable=self.exo_direction_var,
                     values=list(TEXT_DIRECTION_VALUES.keys()), width=8, state="readonly").pack(side="left")
        color_row = ttk.Frame(exo_frame)
        color_row.pack(fill="x", pady=6)
        ttk.Label(color_row, text="", width=16).pack(side="left")
        self._color_swatch_widget(color_row, "文字色", self.exo_color_var)
        self._color_swatch_widget(color_row, "縁取り色", self.exo_color2_var, padx_left=24)
        exo_row3 = ttk.Frame(exo_frame)
        exo_row3.pack(fill="x", pady=3)
        ttk.Label(exo_row3, text="位置X", width=16).pack(side="left")
        ttk.Spinbox(exo_row3, from_=-5000, to=5000, increment=1,
                    textvariable=self.exo_text_x_var, width=10).pack(side="left")
        ttk.Label(exo_row3, text="位置Y", width=8).pack(side="left", padx=(18, 0))
        ttk.Spinbox(exo_row3, from_=-5000, to=5000, increment=1,
                    textvariable=self.exo_text_y_var, width=10).pack(side="left")
        ttk.Label(exo_row3, text="フェードIN", width=8).pack(side="left", padx=(18, 0))
        ttk.Spinbox(exo_row3, from_=0, to=300, textvariable=self.exo_fade_in_var, width=8).pack(side="left")
        ttk.Label(exo_row3, text="OUT", width=4).pack(side="left", padx=(8, 0))
        ttk.Spinbox(exo_row3, from_=0, to=300, textvariable=self.exo_fade_out_var, width=8).pack(side="left")

        preview_frame = ttk.LabelFrame(root, text="プレビュー", padding=10)
        preview_frame.pack(fill="x", pady=(0, 10))
        self.preview_canvas = tk.Canvas(preview_frame, width=480, height=270,
                                        bg="#20242a", highlightthickness=1,
                                        highlightbackground="#555")
        self.preview_canvas.pack(anchor="w")
        self.preview_canvas.bind("<Configure>", lambda _event: self._update_preview())

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(4, 10))
        self.run_button = ttk.Button(buttons, text="全部まとめて実行", command=self.run_pipeline)
        self.run_button.pack(side="left")
        ttk.Button(buttons, text="出力先を開く", command=self.open_output_dir).pack(side="left", padx=8)
        ttk.Button(buttons, text="ログをクリア", command=self.clear_log).pack(side="left")

        log_frame = ttk.LabelFrame(root, text="ログ", padding=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="word", height=20)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log("準備完了")

    def _file_row(self, parent, label: str, var: tk.StringVar, browse_cmd) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=3)
        ttk.Label(frame, text=label, width=16).pack(side="left")
        ttk.Entry(frame, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="参照...", command=browse_cmd).pack(side="left", padx=8)

    def _bind_preview_updates(self) -> None:
        preview_vars = [
            self.exo_width_var,
            self.exo_height_var,
            self.exo_font_var,
            self.exo_direction_var,
            self.exo_font_size_var,
            self.exo_color_var,
            self.exo_color2_var,
            self.exo_border_type_var,
            self.exo_text_x_var,
            self.exo_text_y_var,
        ]
        for var in preview_vars:
            var.trace_add("write", lambda *_args: self._update_preview())
        if self.lyrics_text is not None:
            self.lyrics_text.bind("<KeyRelease>", lambda _event: self._update_preview(), add="+")
        self.after(50, self._update_preview)

    def _preview_text(self) -> str:
        if self.lyrics_text is None:
            return "サンプル歌詞"
        for line in self.lyrics_text.get("1.0", "end").splitlines():
            line = line.strip()
            if line:
                return line
        return "サンプル歌詞"

    def _tk_color(self, hex6: str, fallback: str) -> str:
        value = hex6.strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", value):
            return f"#{value}"
        return fallback

    def _preview_canvas_size(self, video_w: int, video_h: int) -> tuple[int, int]:
        max_w = 520
        max_h = 300
        scale = min(max_w / video_w, max_h / video_h)
        return max(120, int(video_w * scale)), max(120, int(video_h * scale))

    def _draw_preview_text(self, canvas: tk.Canvas, text: str, x: float, y: float,
                           font_name: str, size: int, fill: str, outline: str,
                           direction: str) -> None:
        offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)]
        if direction == "vertical":
            line_height = max(size + 4, int(size * 1.15))
            start_y = y - (len(text) - 1) * line_height / 2
            for index, char in enumerate(text):
                cy = start_y + index * line_height
                for dx, dy in offsets:
                    canvas.create_text(x + dx, cy + dy, text=char, fill=outline,
                                       font=(font_name, size, "bold"), anchor="center")
                canvas.create_text(x, cy, text=char, fill=fill,
                                   font=(font_name, size, "bold"), anchor="center")
            return

        for dx, dy in offsets:
            canvas.create_text(x + dx, y + dy, text=text, fill=outline,
                               font=(font_name, size, "bold"), anchor="center")
        canvas.create_text(x, y, text=text, fill=fill,
                           font=(font_name, size, "bold"), anchor="center")

    def _update_preview(self) -> None:
        canvas = self.preview_canvas
        if canvas is None:
            return
        try:
            video_w = max(1, int(self.exo_width_var.get()))
            video_h = max(1, int(self.exo_height_var.get()))
        except Exception:
            video_w = DEFAULT_EXO_CONFIG["width"]
            video_h = DEFAULT_EXO_CONFIG["height"]

        preview_w, preview_h = self._preview_canvas_size(video_w, video_h)
        if int(canvas.cget("width")) != preview_w or int(canvas.cget("height")) != preview_h:
            canvas.configure(width=preview_w, height=preview_h)

        width = preview_w
        height = preview_h
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#20242a", outline="")
        canvas.create_line(width / 2, 0, width / 2, height, fill="#3c424c")
        canvas.create_line(0, height / 2, width, height / 2, fill="#3c424c")
        canvas.create_rectangle(8, 8, width - 8, height - 8, outline="#606975")

        try:
            scale = min((width - 16) / video_w, (height - 16) / video_h)
            offset_x = (width - video_w * scale) / 2
            offset_y = (height - video_h * scale) / 2
            x = offset_x + video_w * scale / 2 + float(self.exo_text_x_var.get()) * scale
            y = offset_y + video_h * scale / 2 + float(self.exo_text_y_var.get()) * scale
            size = max(8, min(72, int(int(self.exo_font_size_var.get()) * scale * 2.0)))
        except Exception:
            x = width / 2
            y = height / 2
            size = 24

        fill = self._tk_color(self.exo_color_var.get(), "#ffffff")
        outline = self._tk_color(self.exo_color2_var.get(), "#000000")
        font_name = self.exo_font_var.get().strip().lstrip("@") or DEFAULT_EXO_CONFIG["font"]
        direction = TEXT_DIRECTION_VALUES.get(self.exo_direction_var.get(), "horizontal")
        self._draw_preview_text(canvas, self._preview_text(), x, y, font_name, size,
                                fill, outline, direction)

    def _color_swatch_widget(self, parent: tk.Misc, label: str, var: tk.StringVar, padx_left: int = 0) -> None:
        frame = ttk.Frame(parent)
        frame.pack(side="left", padx=(padx_left, 0))
        ttk.Label(frame, text=label, font=("Segoe UI", 9)).pack(anchor="w")
        inner = ttk.Frame(frame)
        inner.pack(anchor="w")
        swatch = tk.Label(inner, width=3, relief="solid", borderwidth=1, cursor="hand2")
        swatch.pack(side="left")
        ttk.Entry(inner, textvariable=var, width=8, font=("Courier New", 10)).pack(side="left", padx=(4, 0))

        def aviutl_to_tk(hex6: str) -> str:
            h = hex6.strip().lstrip("#")
            return "#" + h.upper() if len(h) == 6 else "#FFFFFF"

        def tk_to_aviutl(tk_hex: str) -> str:
            return tk_hex.lstrip("#").lower()

        def contrast_text(hex6: str) -> str:
            try:
                h = hex6.strip().lstrip("#")
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 128 else "#FFFFFF"
            except Exception:
                return "#000000"

        def refresh_swatch(*_args) -> None:
            value = var.get().strip().lstrip("#")
            try:
                swatch.configure(bg=aviutl_to_tk(value), fg=contrast_text(value), text=value[:6].upper() or "------")
            except tk.TclError:
                swatch.configure(bg="white", fg="black", text="------")

        var.trace_add("write", refresh_swatch)
        refresh_swatch()

        def pick_color(_event=None) -> None:
            result = colorchooser.askcolor(color=aviutl_to_tk(var.get()), title=f"{label}を選択")
            if result and result[1]:
                var.set(tk_to_aviutl(result[1]))

        swatch.bind("<Button-1>", pick_color)

    def _load_lyrics_from_file(self) -> None:
        path = filedialog.askopenfilename(title="歌詞TXTを選択",
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            if self.lyrics_text is not None:
                self.lyrics_text.delete("1.0", "end")
                self.lyrics_text.insert("1.0", content)
            if not self.out_dir_var.get():
                self.out_dir_var.set(str(Path(path).parent))

    def clear_lyrics(self) -> None:
        if self.lyrics_text is not None:
            self.lyrics_text.delete("1.0", "end")

    def _browse_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="音声ファイルを選択",
            filetypes=[("Audio files", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac"), ("All files", "*.*")]
        )
        if path:
            self.audio_var.set(path)
            if not self.out_dir_var.get():
                self.out_dir_var.set(str(Path(path).parent))

    def _browse_outdir(self) -> None:
        path = filedialog.askdirectory(title="出力フォルダを選択")
        if path:
            self.out_dir_var.set(path)

    def clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def _queue_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                self.log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _validate_inputs(self) -> tuple[str, str, str]:
        lyrics_text = self.lyrics_text.get("1.0", "end").strip() if self.lyrics_text is not None else ""
        audio_path = self.audio_var.get().strip()
        out_dir = self.out_dir_var.get().strip()
        if not lyrics_text:
            raise ValueError("歌詞を入力してください。")
        if not audio_path:
            raise ValueError("音声ファイルを選んでください。")
        if not out_dir:
            out_dir = str(Path(audio_path).parent)
        if not os.path.exists(audio_path):
            raise ValueError(f"音声ファイルが見つかりません: {audio_path}")
        return lyrics_text, audio_path, out_dir

    def _collect_exo_config(self) -> dict:
        try:
            config = dict(DEFAULT_EXO_CONFIG)
            config.update({
                "width": int(self.exo_width_var.get()),
                "height": int(self.exo_height_var.get()),
                "fps": int(self.exo_fps_var.get()),
                "font": self.exo_font_var.get().strip() or DEFAULT_EXO_CONFIG["font"],
                "text_direction": TEXT_DIRECTION_VALUES.get(self.exo_direction_var.get(), "horizontal"),
                "text_align": DEFAULT_EXO_CONFIG["text_align"],
                "font_size": int(self.exo_font_size_var.get()),
                "color": self.exo_color_var.get().strip().lstrip("#") or DEFAULT_EXO_CONFIG["color"],
                "color2": self.exo_color2_var.get().strip().lstrip("#") or DEFAULT_EXO_CONFIG["color2"],
                "border_type": int(self.exo_border_type_var.get()),
                "text_x": float(self.exo_text_x_var.get()),
                "text_y": float(self.exo_text_y_var.get()),
                "fade_in_frames": int(self.exo_fade_in_var.get()),
                "fade_out_frames": int(self.exo_fade_out_var.get()),
            })
        except Exception as e:
            raise ValueError(f"EXO設定の数値が不正です: {e}") from e

        if config["fps"] <= 0:
            raise ValueError("FPSは1以上にしてください。")
        for key in ("color", "color2"):
            if not re.fullmatch(r"[0-9a-fA-F]{6}", config[key]):
                raise ValueError("色はRRGGBB形式の6桁で入力してください。")
            config[key] = config[key].lower()
        return config

    def run_pipeline(self) -> None:
        if self.running:
            messagebox.showinfo("実行中", "すでに処理中です。")
            return
        try:
            lyrics_text, audio_path, out_dir = self._validate_inputs()
            exo_config = self._collect_exo_config()
            search_window = int(self.window_var.get())
        except Exception as e:
            messagebox.showerror("入力エラー", str(e))
            return
        if whisper is None:
            messagebox.showerror("依存関係エラー", f"whisper の読み込みに失敗しました:\n{_whisper_import_error}")
            return
        if fuzz is None:
            messagebox.showerror("依存関係エラー", f"rapidfuzz の読み込みに失敗しました:\n{_rapidfuzz_import_error}")
            return

        self.running = True
        self.run_button.configure(state="disabled")
        self.log("=== 処理開始 ===")
        self.log(f"Audio: {audio_path}")
        self.log(f"Output: {out_dir}")
        self.log(f"Lyrics chars: {len(lyrics_text)}")

        def finish_ok(result: dict) -> None:
            self._queue_log("")
            self._queue_log("=== 完了 ===")
            self._queue_log(f"LRC : {result['lrc']}")
            self._queue_log(f"JSON: {result['json']}")
            self._queue_log(f"EXO : {result['exo']}")
            self._queue_log(f"一致した行の目安: {result['matched']}/{result['total']}")
            self._queue_log("")
            self.running = False
            self.run_button.configure(state="normal")
            messagebox.showinfo("完了", "LRC / JSON / EXO を出力しました。")

        def finish_error(error: Exception) -> None:
            self._queue_log("")
            self._queue_log(f"[エラー] {error}")
            self.running = False
            self.run_button.configure(state="normal")
            messagebox.showerror("処理エラー", str(error))

        def worker() -> None:
            try:
                result = generate_all(
                    lyrics_text=lyrics_text,
                    audio_path=audio_path,
                    out_dir=out_dir,
                    model_name=self.model_var.get().strip() or MODEL_NAME,
                    search_window=search_window,
                    milliseconds=bool(self.ms_var.get()),
                    write_tags=bool(self.tags_var.get()),
                    title=self.title_var.get().strip() or None,
                    artist=self.artist_var.get().strip() or None,
                    exo_config=exo_config,
                    log=self._queue_log,
                )
            except Exception as e:
                self.after(0, finish_error, e)
            else:
                self.after(0, finish_ok, result)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def open_output_dir(self) -> None:
        path = self.out_dir_var.get().strip()
        if not path:
            messagebox.showinfo("出力先", "出力フォルダが未設定です。")
            return
        if not os.path.exists(path):
            messagebox.showerror("出力先", f"フォルダが見つかりません: {path}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("エラー", str(e))


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
