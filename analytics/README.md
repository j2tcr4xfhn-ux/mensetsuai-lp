# めんせつAI LP アナリティクス

TrendReaction方式を流用した自己完結の計測バックエンド＋ダッシュボード。
ファネル: **lp_view → cta_click → inquiry_submit**、CVR = inquiry_submit / lp_view。

LP（GitHub Pages＝静的）からイベントを受け取り、`/analytics` でCVR・ファネル・流入元・デバイス・流入タグ(?s=)・直近イベントを表示する。

## プライバシー（TrendReaction踏襲）
- ボット(UA)・オーナー(除外Cookie)は計測しない
- 流入元はドメイン単位ラベルのみ／IPは30日で自動削除／トラッキングCookie不使用

## 起動（ローカル確認）
```bash
cd analytics
pip install -r requirements.txt
ANALYTICS_TOKEN=好きな秘密 LP_ORIGIN='*' uvicorn server:app --host 0.0.0.0 --port 8090
```
- ダッシュボード: http://localhost:8090/analytics?token=好きな秘密
- オーナー除外（自分のブラウザを計測から外す）: http://localhost:8090/api/owner-exclude?token=好きな秘密 を1回開く

## 本番デプロイ（例）
GitHub Pagesにはバックエンドを置けないので、別ホストに置く:
- VPS（systemd + uvicorn、リバプロでHTTPS）／ Cloud Run（Dockerfile）／ Fly.io 等
- 環境変数:
  - `ANALYTICS_TOKEN` … ダッシュボード閲覧トークン（必須・推測されない値に）
  - `LP_ORIGIN` … LPのオリジン（例 `https://j2tcr4xfhn-ux.github.io`）。CORSを絞る
  - `ANALYTICS_DB` … SQLiteパス（既定 analytics.db。永続ボリュームに）

## LPとの接続
`b2b.html` の `ANALYTICS_BASE` に本バックエンドのURLを設定すると送信が有効化（空ならconsoleのみ）。
```js
var ANALYTICS_BASE = 'https://<your-analytics-host>';
```
塾別の閲覧を見たいときは、DMのURLに `?s=塾記号` を付ける（例 `...b2b.html?s=A塾`）。`/analytics` の「流入タグ」に出る。

## 注意
- LP公開はApp Store審査通過後が推奨（lp/CLAUDE.md 公開前チェックリスト）。アナリティクス配線は公開直前で可。
- 流出元は本番では `LP_ORIGIN` をGitHub Pagesドメインに固定すること。
