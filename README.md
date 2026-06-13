# Lyrics to EXO

歌詞テキストと音声ファイルから、LRC / timed JSON / AviUtl EXO をまとめて生成するデスクトップアプリです。

## About

このアプリは、歌詞動画制作で面倒になりやすい「歌詞のタイミング合わせ」を補助するためのツールです。Whisperで音声を文字起こしし、歌詞行と認識結果を fuzzy matching で対応付けて、AviUtlの拡張編集で読み込めるEXOファイルとして出力します。

完全な動画編集ソフトではなく、AviUtlに渡す前のタイミングデータ作成とテキストオブジェクト生成に集中しています。

## Features

- 歌詞テキストと音声ファイルからタイミング付きデータを生成
- LRC / timed JSON / AviUtl EXO を一括出力
- フォント、文字サイズ、色、縁取り、位置、フェードをGUIで設定
- 横書き / 縦書きのEXO出力
- 簡易プレビューで文字スタイルと位置を確認

## Requirements

- Python 3.12
- ffmpeg
- `requirements.txt` に記載されたPythonパッケージ

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
- 音声ファイルや生成物はリポジトリに含めない想定です。

## License

This project is licensed under the [MIT License](LICENSE).
