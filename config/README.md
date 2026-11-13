# TechDB とポストプロセッサ

SOLIDWORKS CAM の加工設定（工具・切削条件・ストラテジー）は **TechDB**、NCコードの書式は **ポストプロセッサ** に入っている。
どちらもパーツファイルには入らないので、**パーツファイルだけを受け取っても、作り込んだ設定は渡らない。**
仕組みは [`../docs/cam/01-techdb-basics.md`](../docs/cam/01-techdb-basics.md) を参照。

## ここにあるもの・ないもの

| ファイル | 中身 | 置き場所 | 時点 |
|---|---|---|---|
| `posts/Mill/iwashita_NV2_posts.ctl` | iwashita NV2 用のポストプロセッサ本体（2026年度の担当者が自作） | **このフォルダ** | 2026-09-17 |
| `posts/Mill/iwashita_NV2_posts.lng` | 同上の言語ファイル | **このフォルダ** | 2026-09-17 |
| `posts/src/iwashita_NV2_posts.SRC` | 上のポストのソース（テキスト）。ポストを直すときに使う | **このフォルダ** | 2026-10-03 |
| `posts/src/iwashita_NV2_posts.LIB` | ソースと組になるライブラリ | **このフォルダ** | 2026-10-03 |
| `TechDB.cwdb` | 工具ライブラリ・ツールクリブ・切削条件・ストラテジー・機械定義 `iwashita_NV2` | **リポジトリには置かない。** 管理者から手渡しでもらう | 2026-09-29 |

### TechDB をリポジトリに置かない理由

TechDB は、SOLIDWORKS CAM に最初から入っているデータベースに設定を足していったもので、**メーカーのサンプルデータ（工具ライブラリやストラテジーなど）がそのまま残っている。**
このリポジトリは公開されているので、ここに置くと誰でもダウンロードできてしまい、ソフトの利用規約に触れるおそれがある。
そのため、ファナックのマニュアル（[`../docs/machine/manuals.md`](../docs/machine/manuals.md)）と同じく、**SOLIDWORKS CAM のライセンスを持つ仲間うちだけで手渡しする。**

- TechDB が欲しい人は、このリポジトリの管理者に頼む（USB メモリや班の共有フォルダで受け取る）
- **`TechDB.cwdb` をこのリポジトリにコミットしない。** `.gitignore` で除外してあるが、名前を変えて足したりしない
- TechDB にどんな設定を入れたかは、文章で [`../docs/cam/04-techdb-default-parameters.md`](../docs/cam/04-techdb-default-parameters.md) などに書いてある。TechDB がなくても、これを見れば同じ設定を作り直せる

ポストは2026年度の担当者が自作したものなので、ここに置いている。どちらも SOLIDWORKS CAM のライセンスがないと使えない。

- ポストに作り込んだ内容（手動工具交換の安全シーケンス、`COMP_OFFSET=0` など）は [`../docs/cam/05-post-and-tool-change.md`](../docs/cam/05-post-and-tool-change.md)

## 自分の PC に入れる

1. **SolidWorks を終了する**（起動中は TechDB がロックされて上書きできない）
2. 今の PC の TechDB とポストを、念のためバックアップする（下の「バックアップの取り方」）
3. 管理者からもらった TechDB と、このフォルダのポストを次の場所にコピーする

```
TechDB.cwdb（手渡しでもらう）    →  C:\ProgramData\SOLIDWORKS\SOLIDWORKS CAM 2026\TechDB\TechDB.cwdb
config/posts/Mill/iwashita_*    →  C:\ProgramData\SOLIDWORKS\SOLIDWORKS CAM 2026\Posts\Mill\
```

4. SolidWorks を起動し、CAM の機械定義で `iwashita_NV2` を選ぶ。ポストに `iwashita_NV2_posts` が割り当てられているか確認する
5. 簡単な部品で試しにポスト処理し、[`05-post-and-tool-change.md`](../docs/cam/05-post-and-tool-change.md) の「ポスト処理のたびに確認すること」を一通り見る

SOLIDWORKS CAM のバージョンが 2026 でない場合は、フォルダ名が変わる。新しいバージョンで古い TechDB を開くと変換を求められることがあるので、元のファイルを残してから行う。

## 更新したとき

- **ポストを直したら**、SolidWorks を終了してから `C:\ProgramData\...\Posts\Mill\` のファイルをこのフォルダにコピーし、上の表の「時点」を書き換えてコミットする。**ソース（`C:\CAMWorksData\UPG-2\MyPosts\` の `.SRC` と `.LIB`）も一緒に `posts/src/` へコピーする**（ソースとコンパイル後のファイルがずれないように）
- **TechDB を直したら**、SolidWorks を終了してから `TechDB.cwdb` を管理者に渡し、仲間うちで配る最新版を差し替えてもらう。リポジトリには、上の表の「時点」と、**変えた設定の内容を文章で** `docs/cam/` の該当ファイルに書く（TechDB の中身は外から読めないので、文章が唯一の記録になる）
- 工具を足したら、ツールクリブの番号を [`05-post-and-tool-change.md`](../docs/cam/05-post-and-tool-change.md) の「工具番号の割り当て」にも書く

## バックアップの取り方

SolidWorks を終了してから、PowerShell で次を実行する（`<user>` は自分のユーザー名）。

```powershell
Copy-Item "C:\ProgramData\SOLIDWORKS\SOLIDWORKS CAM 2026\TechDB","C:\ProgramData\SOLIDWORKS\SOLIDWORKS CAM 2026\Posts" -Destination "C:\Users\<user>\Documents\SW_CAM_backup\$(Get-Date -Format yyyy-MM-dd)" -Recurse -Force
```

- 取り直すタイミング: 工具ライブラリを整備した日、ポストを編集した日、TechDB を SQL で書き換える前
- 復元はコピーを逆向きに戻すだけ（SolidWorks を終了した状態で行う）

## 未確定事項

- ポストの元になったソースファイル（`.SRC` と `.LIB`。`docs/cam/05` に出てくる `CALC_INIT_CODES` はこの中にある）は、2026年度の担当者の PC の `C:\CAMWorksData\UPG-2\MyPosts\` にあり、2026-11-13 に `posts/src/` へコピーした。ポストを直すときは、これを自分の PC の同じ場所（`C:\CAMWorksData\UPG-2\MyPosts\`）に置いて使う。ソースの読み方と直し方は [`../docs/cam/05-post-and-tool-change.md`](../docs/cam/05-post-and-tool-change.md) の「ポストの構造と直し方（UPG）」
- ソースは 2026-10-03、`.ctl` は 2026-09-17 の日付で、**ソースのほうが新しい。** 2026-09-17 の変更直前のバックアップと比べると、違いは退避高さを `Z30.` にした変更（`.ctl` にも入っていることを確認済み）と、初期値の設定4行が重複して増えていることだけだった。重複した行は同じ値をもう一度入れているだけなので出力は変わらないはずだが、**このソースをコンパイルし直して同じNCコードが出るかは確かめていない**
