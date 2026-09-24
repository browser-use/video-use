[English](README.md) | [简体中文](README.zh-CN.md) | **日本語**

<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

**video-use** を紹介します。Claude Code で動画を編集できる、100% オープンソースのツールです。

生の映像素材をフォルダーに入れて Claude Code と対話すると、`final.mp4` が返ってきます。トーキングヘッド、モンタージュ、チュートリアル、旅行、インタビューなど、あらゆるコンテンツに対応し、プリセットやメニューは不要です。

[Browser Use Cloud](https://cloud.browser-use.com/v4?utm_campaign=video-use-use-in-cloud&utm_source=github) で video-use を試せます。

## できること

- **フィラーワードを削除**（`umm`、`uh`、言い直し）し、テイク間の無音部分も取り除きます
- **各セグメントを自動カラーグレーディング**（暖かみのあるシネマ風、ニュートラルで力強い仕上げ、または任意のカスタム ffmpeg チェーン）
- **各カットに 30 ミリ秒のオーディオフェード**を適用し、ポップノイズを防ぎます
- **好みのスタイルで字幕を焼き込み**ます。デフォルトは 2 語ずつの大文字表示で、全面的にカスタマイズできます
- **[HyperFrames](https://github.com/heygen-com/hyperframes)、[Remotion](https://www.remotion.dev/)、[Manim](https://www.manim.community/)、または PIL でアニメーションオーバーレイを生成**します。アニメーションごとに 1 つのサブエージェントを並列で起動します
- **表示前に、すべてのカット境界でレンダリング結果を自己評価**します
- **セッションの記憶を `project.md` に保存**し、翌週のセッションでも前回の続きから再開できます

## セットアップ用プロンプト

Claude Code、Codex、Hermes、Openclaw、またはシェルにアクセスできる任意のエージェントに、次の内容を貼り付けます。

```text
Set up https://github.com/browser-use/video-use for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

エージェントがクローン、依存関係、スキル登録を処理し、ElevenLabs API キーの入力を一度だけ求めます（キーは [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) で取得できます）。

次に、生のテイクが入ったフォルダーをエージェントに指定します。

```bash
cd /path/to/your/videos
claude    # or codex, hermes, etc.
```

自分の VPS や Telegram から常時編集するには、[Browser Use Box](https://browser-use.com/bux) 経由でエージェントを実行します。[15 秒のデモを見る](https://www.tiktok.com/@browser_use/video/7639824093721758989)。

セッションでは次のように入力します。

> これらをローンチ動画に編集して

エージェントは素材を確認して戦略を提案し、承認を待ってから、素材の隣に `edit/final.mp4` を生成します。すべての出力は `<videos_dir>/edit/` に保存されるため、スキルのディレクトリはクリーンに保たれます。

## 手動インストール

手作業で行う場合は、次の手順を使用します。

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/browser-use/video-use ~/Developer/video-use
ln -sfn ~/Developer/video-use ~/.claude/skills/video-use        # Claude Code
# ln -sfn ~/Developer/video-use ~/.codex/skills/video-use       # Codex

# 2. Install deps
cd ~/Developer/video-use
uv sync                         # or: pip install -e .
brew install ffmpeg             # required
brew install yt-dlp             # optional, for downloading online sources

# 3. Add your ElevenLabs API key
cp .env.example .env
$EDITOR .env                    # ELEVENLABS_API_KEY=...
```

## 仕組み

LLM は動画を視聴しません。2 つのレイヤーを通じて動画を**読み取り**、単語の境界に正確に合わせてカットするために必要な情報をすべて取得します。

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view の合成表示—フィルムストリップ、話者トラック、波形、単語ラベル、無音区間のカット候補" width="100%">
</p>

**レイヤー 1 — 音声トランスクリプト（常時読み込み）。** ソースごとに ElevenLabs Scribe を 1 回呼び出すことで、単語単位のタイムスタンプ、話者ダイアライゼーション、音声イベント（`(laughter)`、`(applause)`、`(sigh)`）を取得します。すべてのテイクは 1 つの約 12KB の `takes_packed.md` にまとめられ、これが LLM の主要な読み取りビューになります。

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**レイヤー 2 — ビジュアル合成（オンデマンド）。** `timeline_view` は任意の時間範囲について、フィルムストリップ、波形、単語ラベルを組み合わせた PNG を生成します。曖昧な間、撮り直しの比較、カット位置の妥当性確認といった判断ポイントでのみ呼び出されます。

> 単純な方法：30,000 フレーム × 1,500 トークン = **4,500 万トークンのノイズ**。
> Video Use：**12KB のテキスト + 少数の PNG**。

browser-use がスクリーンショットではなく構造化 DOM を LLM に渡すのと同じ発想を、動画に適用しています。

## パイプライン

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Render ──> Self-Eval
                                                              │
                                                              └─ issue? fix + re-render (max 3)
```

自己評価ループは各カット境界で_レンダリング済みの出力_に `timeline_view` を実行し、映像のジャンプ、オーディオのポップノイズ、隠れた字幕を検出します。チェックを通過した後にだけプレビューが表示されます。

## 設計原則

1. **テキスト + オンデマンドのビジュアル。** フレームを大量投入せず、トランスクリプトを操作面として使います。
2. **音声を優先し、映像を追従させます。** カット位置は発話境界と無音区間から決まります。
3. **質問 → 確認 → 実行 → 自己評価 → 永続化。** 戦略が承認されるまで、カットには手を加えません。
4. **コンテンツ種別を一切決めつけません。** 見て、質問してから編集します。
5. **12 個の厳格なルール、それ以外には表現の自由。** 制作上の正しさは妥協せず、好みは自由です。

完全な制作ルールと編集技法については、[`SKILL.md`](./SKILL.md) を参照してください。
