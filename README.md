# USB カメラサーバー

Web インターフェースと管理 API を備えたプロダクション対応 USB カメラストリーミングサーバー

[English README](README_EN.md) | 日本語

## 🎯 概要

Python、FastAPI、OpenCV で構築された高信頼性 USB カメラストリーミングサービスです。安定した継続的なカメラストリーミングと包括的な監視・管理機能を必要とするプロダクション環境向けに設計されています。

## ✨ 機能

### コア機能
- 📹 **リアルタイム USB カメラストリーミング** - HTTP 経由での MJPEG 動画配信
- 🌐 **Web インターフェース** - ライブコントロール付きモダンなレスポンシブ Web UI
- 🔄 **自動再接続** - 設定可能な再試行ロジックによる自動カメラ再接続
- 📊 **システム監視** - リアルタイム統計とヘルス監視
- ⚙️ **設定管理** - ホットリロード対応の JSON ベース設定

### 管理・制御
- 🔌 **RESTful API** - カメラとサーバー制御のための完全な管理 API
- 📡 **シグナル処理** - プロセス管理のための Unix シグナルサポート
- 📝 **包括的ログ** - ファイルとコンソール出力による構造化ログ
- 🛡️ **エラー回復** - 堅牢なエラーハンドリングと回復メカニズム
- 🔧 **実行時設定** - 再起動なしでの設定更新

### プロダクション機能
- 🚀 **高パフォーマンス** - キューバッファリングによる最適化されたフレーム処理
- 🔒 **セキュリティ** - CORS と信頼できるホストミドルウェアサポート
- 📈 **スケーラビリティ** - 設定可能なフレーム品質とバッファ管理
- 🐳 **コンテナ対応** - 最小限の依存関係での簡単デプロイ

## 🚀 クイックスタート

### 前提条件
- Python 3.8+
- USB カメラデバイス
- Linux/macOS/Windows サポート

### インストール

1. **リポジトリをクローン**
   ```bash
   git clone https://github.com/yourusername/usb-camera-server.git
   cd usb-camera-server
   ```

2. **依存関係をインストール**
   ```bash
   pip install -r requirements.txt
   ```

3. **サーバーを実行**
   ```bash
   python main.py
   ```

4. **Web インターフェースにアクセス**
   ブラウザで http://localhost:8000 を開く

## 📋 設定

サーバーは初回実行時にデフォルト値で自動作成される JSON 設定ファイル（`config.json`）を使用します。

### カメラ設定
```json
{
  "camera": {
    "device_index": 0,
    "device_path": "/dev/video0",
    "width": 640,
    "height": 480,
    "fps": 30,
    "buffer_size": 2,
    "jpeg_quality": 80,
    "auto_reconnect": true,
    "reconnect_interval": 5,
    "max_reconnect_attempts": 10
  }
}
```

### サーバー設定
```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000,
    "log_level": "info",
    "log_file": "camera_stream.log",
    "cors_origins": ["*"],
    "trusted_hosts": ["*"],
    "max_frame_age": 5
  }
}
```

## 🔧 API リファレンス

### コアエンドポイント

| メソッド | エンドポイント | 説明 |
|---------|---------------|------|
| `GET` | `/` | Web インターフェース |
| `GET` | `/video_feed` | MJPEG 動画ストリーム |
| `GET` | `/status` | システム状態と統計 |
| `GET` | `/health` | ヘルスチェックエンドポイント |

### 管理エンドポイント

| メソッド | エンドポイント | 説明 |
|---------|---------------|------|
| `POST` | `/camera/restart` | カメラサービス再起動 |
| `POST` | `/server/reload-config` | 設定ファイル再読み込み |
| `POST` | `/server/shutdown` | グレースフルシャットダウン |
| `GET` | `/server/pid` | プロセス情報 |
| `GET` | `/config` | 現在の設定取得 |
| `PUT` | `/config` | 設定更新 |

### ステータスレスポンス例
```json
{
  "is_running": true,
  "is_connected": true,
  "queue_size": 1,
  "reconnect_attempts": 0,
  "stats": {
    "frames_captured": 1250,
    "frames_dropped": 3,
    "connection_errors": 0,
    "uptime": 125.45,
    "last_frame_age": 0.033
  },
  "config": {
    "device_index": 0,
    "width": 640,
    "height": 480
  }
}
```

## 🔄 シグナル処理（Unix/Linux）

サーバーはプロセス管理のための Unix シグナルをサポートしています：

| シグナル | アクション | 説明 |
|---------|-----------|------|
| `SIGINT` | シャットダウン | グレースフルシャットダウン（Ctrl+C）|
| `SIGTERM` | シャットダウン | グレースフルシャットダウン（kill）|
| `SIGHUP` | リロード | 設定ファイル再読み込み |
| `SIGUSR1` | 統計 | ログに統計情報を出力 |
| `SIGUSR2` | 再起動 | カメラサービス再起動 |

### 使用例
```bash
# プロセス ID を取得
curl localhost:8000/server/pid

# 設定を再読み込み
kill -HUP <pid>

# 統計情報を表示
kill -USR1 <pid>

# カメラを再起動
kill -USR2 <pid>
```

## 🛠️ 開発

### プロジェクト構造
```
usb-camera-server/
├── main.py              # メインアプリケーションファイル
├── requirements.txt     # Python 依存関係
├── config.json         # 設定ファイル（自動生成）
├── README.md           # このファイル（日本語）
└── README_EN.md        # 英語版 README
```

### 主要コンポーネント

#### CameraManager
- カメラの初期化とフレームキャプチャを処理
- 自動再接続ロジックを実装
- 設定可能なキューサイズでフレームバッファリングを管理
- 包括的な統計と監視を提供

#### ConfigManager
- JSON ベースの設定管理
- 自動デフォルト設定生成
- 実行時設定更新

#### SignalHandler
- グレースフルシャットダウン処理
- 管理操作のための Unix シグナル処理
- リソースクリーンアップと適切な終了

### 開発環境セットアップ

1. **仮想環境を作成**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   # または
   venv\Scripts\activate     # Windows
   ```

2. **開発用依存関係をインストール**
   ```bash
   pip install -r requirements.txt
   ```

3. **開発モードで実行**
   ```bash
   # デバッグログ付きで実行
   python main.py

   # ログを確認
   tail -f camera_stream.log
   ```

### カメラセットアップのテスト

1. **利用可能なカメラをリスト**
   ```bash
   # Linux
   ls /dev/video*

   # macOS
   system_profiler SPCameraDataType

   # Windows
   # デバイスマネージャーを使用
   ```

2. **カメラアクセスをテスト**
   ```python
   import cv2
   cap = cv2.VideoCapture(0)  # 必要に応じてインデックスを変更
   ret, frame = cap.read()
   print(f"カメラアクセス可能: {ret}")
   cap.release()
   ```

## 🐳 Docker デプロイ

### Dockerfile 例
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .
EXPOSE 8000

# カメラデバイスアクセスを確保
RUN apt-get update && apt-get install -y \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "main.py"]
```

### Docker 実行
```bash
# イメージをビルド
docker build -t usb-camera-server .

# カメラデバイスアクセス付きで実行
docker run -p 8000:8000 --device=/dev/video0 usb-camera-server
```

## 🔍 監視・ログ

### ログレベル
- `DEBUG`: 詳細なデバッグ情報
- `INFO`: 一般的な情報（デフォルト）
- `WARNING`: 警告メッセージ
- `ERROR`: エラー状態
- `CRITICAL`: 重要なエラー

### ログフォーマット
```
2024-01-15 10:30:45,123 - main.CameraManager - INFO - Camera started successfully
```

### 監視エンドポイント
- `/health` - 基本ヘルスチェック
- `/status` - 詳細なシステム状態
- Web インターフェースでのリアルタイム統計

## 🚨 トラブルシューティング

### よくある問題

**カメラが検出されない**
```bash
# デバイス権限を確認
ls -l /dev/video*
# ユーザーを video グループに追加
sudo usermod -a -G video $USER
```

**ポートが既に使用中**
```bash
# ポートを使用しているプロセスを確認
sudo netstat -tulpn | grep :8000
# プロセスを終了するか設定でポートを変更
```

**アクセス拒否**
```bash
# 適切な権限で実行
sudo python main.py
# またはデバイス権限を修正
sudo chmod 666 /dev/video0
```

**高 CPU 使用率**
- 設定でフレームレートを下げる
- JPEG 品質設定を下げる
- バッファサイズを調整

## 📈 パフォーマンスチューニング

### 最適化のコツ

1. **フレームレート**: CPU 使用率を下げるために FPS を低く設定
2. **解像度**: パフォーマンス向上のために小さな解像度を使用
3. **JPEG 品質**: 品質と帯域幅のバランスを調整
4. **バッファサイズ**: ネットワーク状況に基づいて調整
5. **最大フレーム経過時間**: 古いフレームのフィルタリングを設定

### リソース監視
```bash
# CPU とメモリ使用量
top -p $(pgrep -f main.py)

# ネットワーク接続
ss -tulpn | grep :8000

# ディスク I/O（ログ）
iotop
```

## 🤝 貢献

1. リポジトリをフォーク
2. 機能ブランチを作成
3. 変更を実装
4. 該当する場合はテストを追加
5. プルリクエストを送信

### コードスタイル
- PEP 8 ガイドラインに従う
- 可能な場合は型ヒントを使用
- 関数とクラスに docstring を追加
- 既存のエラーハンドリングパターンを維持

## 📄 ライセンス

このプロジェクトは MIT ライセンスの下でライセンスされています - 詳細については LICENSE ファイルを参照してください。

## 🔗 関連プロジェクト

- [OpenCV](https://opencv.org/) - コンピュータビジョンライブラリ
- [FastAPI](https://fastapi.tiangolo.com/) - モダン Web フレームワーク
- [Uvicorn](https://www.uvicorn.org/) - ASGI サーバー

## 📞 サポート

- バグレポートは Issue を作成
- 機能要求は GitHub Issues で送信
- 解決策については既存の Issue を確認