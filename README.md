# Lyrics to EXO

歌詞テキストと音声ファイルから、LRC / timed JSON / AviUtl EXO をまとめて生成するWindows向けデスクトップアプリです。

Whisperで音声を文字起こしし、歌詞行と認識結果を fuzzy matching で対応付けて、AviUtlの拡張編集で読み込める歌詞テキストオブジェクトを出力します。

## About

歌詞動画を作るときに面倒になりやすい「歌詞のタイミング合わせ」を補助するためのツールです。

完全な動画編集ソフトではなく、AviUtlに渡す前のタイミングデータ作成と、EXO用のテキストオブジェクト生成に集中しています。背景画像や映像編集はAviUtl側で行い、このアプリでは歌詞の同期と表示設定を扱います。

## Features

- 歌詞テキストと音声ファイルからタイミング付きデータを生成
- LRC / timed JSON / AviUtl EXO を一括出力
- Whisperによる日本語音声の文字起こし
- fuzzy matching による歌詞行と認識結果の対応付け
- フォント、文字サイズ、文字色、縁取り色、位置、フェードをGUIで設定
- 横書き / 縦書きのEXO出力
- 簡易プレビューで文字スタイルと位置を確認

## Download

Windowsで使う場合は、GitHub Releases から最新版のzipをダウンロードしてください。

[Releases](https://github.com/Haruru1/lyrics-to-exo/releases)

zipを展開して、フォルダ内の `LyricsToEXO.exe` を実行します。

## Usage

1. 歌詞をテキスト欄に入力するか、TXTファイルから読み込みます。
2. 音声ファイルを選択します。
3. 出力フォルダを指定します。
4. フォント、色、位置、縦書き/横書きなどのEXO表示設定を調整します。
5. `全部まとめて実行` を押します。

出力されるファイル:

- `.lrc`
- `timed.json`
- `.exo`

## Requirements

exe版を使う場合:

- Windows
- ffmpeg

Pythonから起動する場合:

- Python 3.12
- ffmpeg
- `requirements.txt` に記載されたPythonパッケージ

## Setup For Development

```powershell
py -3.12 -m pip install -r requirements.txt
```

## Run From Source

Windowsでは `run_gui.bat` をダブルクリックするか、次のコマンドで起動します。

```powershell
py -3.12 app.py
```

## Notes

- Whisperの初回実行時はモデルのダウンロードが必要です。
- ffmpegが見つからない場合、音声読み込みに失敗することがあります。
- 生成されたEXOは AviUtl / 拡張編集での利用を想定しています。
- 音声ファイルや生成物はリポジトリに含めない想定です。
- exe版はWhisper / Torchを含むため、ファイルサイズが大きくなります。

## License

This project is licensed under the [MIT License](LICENSE).
