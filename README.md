# Lyrics to EXO GUI

歌詞テキストと音声ファイルから、LRC / timed JSON / AviUtl EXO をまとめて生成するデスクトップツールです。

Whisperで音声を文字起こしし、歌詞行と認識結果を fuzzy matching で対応付けて、AviUtlに読み込める歌詞テキストオブジェクトを出力します。

## Features

- 歌詞テキストと音声ファイルからタイミング付きデータを生成
- LRC、timed JSON、AviUtl EXOを一括出力
- フォント、文字サイズ、色、縁取り、位置、フェードをGUIで設定
- 横書き / 縦書きのEXO出力
- 簡易プレビューで文字スタイルと位置を確認

## Requirements

- Python 3.12
- ffmpeg
- Python packages listed in `requirements.txt`

## Setup

```powershell
py -3.12 -m pip install -r requirements.txt
```

## Run

Windowsでは `run_gui.bat` をダブルクリックするか、次のコマンドで起動します。

```powershell
py -3.12 app.py
```

## Notes

- Whisperの初回実行時はモデルのダウンロードが必要です。
- 生成されたEXOはAviUtl / 拡張編集での利用を想定しています。

