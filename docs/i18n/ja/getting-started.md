<!-- これは docs/getting-started.md（翻訳元コミット: a7b9cee）のコミュニティによって維持されている翻訳です。英語版が正式版です。 -->

# はじめに

<!-- Installation section synchronized with docs/getting-started.md at e19f502. -->

## インストール

Python 3.10〜3.12 と、確認済みコミットの完全な SHA を使用してください。自社製の 8 パッケージは、完全な一式としてはまだ PyPI に公開されていません。マニフェストのインストーラーでリポジトリのソースをまとめてインストールします。ダッシュボード、ウィザード、チャネル、MCP サーバー、知識・学習パッケージが含まれます。`release-runtime` は OpenAI 互換クライアント、`evidence-analysis` は PDF と表計算ファイルのパーサーを追加します。

macOS / Linux:

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork init
lightwork dashboard
```

Windows PowerShell では、先にコミットのプレースホルダーを置き換えてください。

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip==26.2.1
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe init
.\.venv\Scripts\lightwork.exe dashboard
```

実行ファイルのパスを明示するため、PowerShell の有効化ポリシーを変更する必要はありません。ウィザードでプロバイダーとサンドボックスを設定してください。ソースからのインストールでは、外部アカウント、API クレジット、コンテナーエンジン、Kubernetes クラスターは用意されません。顧客業務に使う前に、許可された実際の操作で連携を確認してください。

同じ環境の Python でパッケージ一式を確認します。

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```

Windows では、実行ファイル名を `.\.venv\Scripts\python.exe` と `.\.venv\Scripts\lightwork.exe` に置き換えてください。

認証付きのデスクトップ用ソースインストールには、確認済みコミットから `deploy/desktop/install.sh` または `deploy/desktop/install.ps1` を取得し、その小文字の完全な SHA を `MAVERICK_REF` に設定します。これはリポジトリへのアクセスと各プラットフォームの前提条件を必要とするソースインストールであり、単独で動く署名済み製品インストーラーではありません。スクリプトは変更可能な参照を拒否し、自社製パッケージを公開インデックスから代替取得しません。`MAVERICK_*` 変数は `LIGHTWORK_*` とも表記できます。

## 初回実行

```bash
maverick init
```

ウィザードの所要時間は 2 分ほどです。`~/.maverick/config.toml` と `~/.maverick/.env` を書き出します。

次に、以下を実行します。

```bash
maverick start "Build a CLI that emails me a digest of today's top Hacker News stories — research the API, write it, and verify it runs"
```

## スウォームが目標を分解する様子を見る

2 つ目のターミナルで `maverick monitor` を実行します。オーケストレーターが目標の計画を立て、並列で動作する専門のサブエージェントを起動します。この例では、リサーチャーが API を特定し、コーダーがツールを書き、ベリファイアがそれを実行して確認します。

```
Goal #1 active  2m elapsed
Build a CLI that emails me a digest of today's top Hacker News stories

Plan tree
  ├─        done  #2 Research the Hacker News Firebase API
  ├─      active  #3 Write the digest CLI (fetch + format + send)
  ├─      active  #4 Verify it runs and emails a sample digest
  ├─     pending  #5 Write a short usage README

Latest episode #7 (running)  $0.0431  in=18,204 out=2,910 tools=11

Recent activity
  4s ago [researcher] decision: top stories live at /v0/topstories.json, then /v0/item/<id>.json
  3s ago [coder] tool_call: write_file hn_digest.py (118 lines)
  1s ago [verifier] tool_call: run "python hn_digest.py --dry-run" -> printed 10 stories

Cumulative spend on this DB: $0.21
```

完了したら:

```bash
maverick status      # what's currently active or blocked
maverick skills      # what the swarm distilled from this run
maverick facts       # what it learned about you
```

## 一時停止と再開

あなたにしか答えられないことが必要になると、スウォームは一時停止し、質問をキューに入れます。

```bash
maverick status
# shows: open questions: #3 (goal 1): Which dates are you traveling?

maverick answer 3 "May 15-29"
maverick resume
```

目標は再起動後も保持されます。ノート PC を閉じて、明日また戻ってきても問題ありません。

## 実行によるファイル編集を元に戻す

ワークスペースジャーナルを有効にすると（`[workspace] journal = true`、または
ウィザードの *Workspace journal* ステップで「はい」を選択）、エージェントが
ファイルツールで上書き・作成するすべてのファイルが、変更の前に記録されます:

```bash
maverick journal list --goal 12     # 何が変更されたか
maverick journal diff --goal 12     # 実行前の状態と現在のディスクの差分
maverick journal revert --goal 12   # 新しい順にすべてのファイルを復元
```

リバートは署名付きの `workspace_revert` 監査行を書き込み、ジャーナルを
アーカイブします。シェル経由の変更（`rm`、`mv`、リダイレクト）はジャーナルに
記録されません — それらのコマンドは監査証跡に記録されます。

## 観察したタスクから自分専用のスペシャリストを作る

仕事を言葉で説明する必要はありません。実際にやって見せることもできます。誰かが作業をしている様子（取った行動と、その理由についての説明）を順序付きの記録として JSONL かシンプルな接頭辞付きテキストで取得し、そのファイルを Lightwork に渡してください。

```
ACTION[gmail]: send the morning digest -> ops@acme.com
NOTE: only the top 5 stories, with one-line summaries
SEE: digest looks right, ops confirmed receipt
```

```bash
maverick learn-demo demo.txt
```

これはデモンストレーションを解析し、ドラフトのスペシャリストを生成し、導出されたワークフローを表示して、保存する前にあなたの承認を待ちます。シークレットは入り口で秘匿化され、ドラフトは説明から作成したパックと同じ機能の制限とペルソナスキャンを引き継ぎます。あなたが「はい」と言わない限り、何も有効化されません。便利なフラグ:

```bash
maverick learn-demo demo.txt --name "Morning Digest" --no-llm --yes
```

`--no-llm` は観察されたステップを決定論的にそのまま再現します（ツール = その人が使ったもの）。これを外すと、モデルがトランスクリプトから提案できるようになります。

同じエージェントファクトリーのフローは、対話形式でパックを構築するときにも実行されます。

```bash
maverick onboard
```

承認すると、`onboard` はパックをプロビジョニングするようになりました。ワークフローに必要なカタログスキルをインストールし、ビルトインではない宣言済みツールを合成するので、承認されたばかりのスペシャリストは最初の実行からその仕事をこなす準備が整っています。（このステップは `[self_learning]` / `provision_packs` の設定を尊重し、パックの制限された範囲を広げることは決してありません。）

## 音声入力(内蔵・オフライン)

ダッシュボードのマイクボタンと `transcribe_audio` ツールは、プロバイダーの
API キーなしで動作します。ローカル Whisper エンジンはダッシュボード
パッケージに同梱され、チェックサム検証済みモデルは初回起動時に自動取得
されます(エグレスロックされた環境や `[voice] auto_fetch_model = false`
ではスキップ)。詳細は CLI で確認できます:

```bash
maverick voice setup    # チェックサム検証済みモデルを取得(約148 MB)
maverick voice status   # 利用可能な音声認識バックエンドを確認
maverick voice transcribe clip.wav   # パイプラインの動作確認
```

システムのエンジンを使いたい場合は、PATH 上の `whisper.cpp` バイナリ
(`brew install whisper-cpp`)や faster-whisper も同じチェーンに組み込めます。
`[voice] stt_backend = "local"` を設定すると、OpenAI/Groq のキーがあっても
音声はマシンの外に出ません。

## モデルやプロバイダーの変更

ウィザードはいつでも再実行できます。

```bash
maverick init
```

または、`~/.maverick/config.toml` を直接編集してください。`[models]` セクションは、各エージェントロールを `provider:model-id` 形式の文字列に対応付けます。スキーマの詳細は [`configuration.md`](../../configuration.md) を参照してください。

## データの保存場所

| ファイル | 内容 |
|---|---|
| `~/.maverick/config.toml` | 設定（デプロイ、モデル、安全性、予算） |
| `~/.maverick/.env` | API キー（chmod 600） |
| `~/.maverick/world.db` | 永続的なワールドモデル: 目標、事実、エピソード |
| `~/.maverick/skills/` | 成功した実行から自動的に蒸留された SKILL.md ファイル |
| `~/maverick-workspace/` | サンドボックスのデフォルト作業ディレクトリ |
| `~/.maverick/learned-skills/` | 学習ループによって蒸留されたスキル |
| `~/.maverick/dreams/` | 統合された知見、リハーサルキュー、学習スナップショット |

すべてのデータはローカルに保存されます。選択したクラウド LLM に送信されるプロンプトを除き、何もアップロードされません。

ある程度の実行を重ねたら、学習面は 4 つのコマンドに集約されます。`maverick dream`（経験を統合する）、`maverick hindsight`（学習は役立ったか、それとも後退したか？）、`maverick proof`（成果物、回避できたコスト、ROI）、そして `maverick domains-lint`（2,020 エージェントのスペシャリストカタログを監査する）に加えて、`maverick domains-audit`（ガバナンス態勢: 各エージェントが何にアクセスでき、何を拒否し、何を却下するか）と `maverick domains-eval --check`（振る舞いのゴールデンケース）です。
