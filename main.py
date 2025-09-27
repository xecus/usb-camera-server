import cv2
import asyncio
import os
import json
import logging
import time
import signal
import sys
import atexit
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
from threading import Thread, Event, Lock
import queue

# ===== 設定管理 =====
@dataclass
class CameraConfig:
    """カメラ設定"""
    device_index: int = 0
    device_path: str = "/dev/video0"
    width: int = 640
    height: int = 480
    fps: int = 30
    buffer_size: int = 2
    jpeg_quality: int = 80
    auto_reconnect: bool = True
    reconnect_interval: int = 5
    max_reconnect_attempts: int = 10

@dataclass
class ServerConfig:
    """サーバー設定"""
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    log_file: Optional[str] = "camera_stream.log"
    cors_origins: list = None
    trusted_hosts: list = None
    max_frame_age: int = 5  # 秒

@dataclass
class AppConfig:
    """アプリケーション設定"""
    camera: CameraConfig
    server: ServerConfig
    
    def __post_init__(self):
        if self.server.cors_origins is None:
            self.server.cors_origins = ["*"]
        if self.server.trusted_hosts is None:
            self.server.trusted_hosts = ["*"]

class ConfigManager:
    """設定ファイル管理"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = self.load_config()
    
    def load_config(self) -> AppConfig:
        """設定ファイルの読み込み"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                camera_config = CameraConfig(**data.get('camera', {}))
                server_config = ServerConfig(**data.get('server', {}))
                return AppConfig(camera=camera_config, server=server_config)
                
            except Exception as e:
                logging.warning(f"Failed to load config from {self.config_path}: {e}")
                logging.info("Using default configuration")
        
        # デフォルト設定で新しい設定ファイルを作成
        config = AppConfig(
            camera=CameraConfig(),
            server=ServerConfig()
        )
        self.save_config(config)
        return config
    
    def save_config(self, config: AppConfig):
        """設定ファイルの保存"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'camera': asdict(config.camera),
                    'server': asdict(config.server)
                }, f, indent=2, ensure_ascii=False)
            logging.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logging.error(f"Failed to save config: {e}")

# ===== ロギング設定 =====
def setup_logging(log_level: str, log_file: Optional[str] = None):
    """ロギングの設定"""
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # ログフォーマット
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # コンソールハンドラー
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    handlers = [console_handler]
    
    # ファイルハンドラー
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception as e:
            print(f"Warning: Failed to setup file logging: {e}")
    
    # ルートロガー設定
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True
    )

# ===== カメラ管理 =====
class CameraManager:
    """高信頼性カメラマネージャー"""
    
    def __init__(self, config: CameraConfig):
        self.config = config
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_running = Event()
        self.is_connected = Event()
        self.thread: Optional[Thread] = None
        self.frame_queue = queue.Queue(maxsize=config.buffer_size)
        self.lock = Lock()
        self.last_frame_time = 0
        self.reconnect_attempts = 0
        self.stats = {
            'frames_captured': 0,
            'frames_dropped': 0,
            'connection_errors': 0,
            'last_reconnect': None,
            'uptime_start': time.time()
        }
        
        self.logger = logging.getLogger(f"{__name__}.CameraManager")
    
    def start(self) -> bool:
        """カメラ開始"""
        if self.is_running.is_set():
            self.logger.warning("Camera is already running")
            return True
        
        if self._initialize_camera():
            self.is_running.set()
            self.thread = Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            self.logger.info("Camera started successfully")
            return True
        
        return False
    
    def stop(self):
        """カメラ停止"""
        if not self.is_running.is_set():
            self.logger.info("Camera is already stopped")
            return
            
        self.logger.info("Stopping camera...")
        self.is_running.clear()
        self.is_connected.clear()
        
        # スレッドの安全な終了を待機
        if self.thread and self.thread.is_alive():
            self.logger.info("Waiting for capture thread to finish...")
            self.thread.join(timeout=10)  # タイムアウトを10秒に延長
            if self.thread.is_alive():
                self.logger.warning("Capture thread did not finish gracefully")
        
        # カメラリソースの解放
        with self.lock:
            if self.cap:
                try:
                    self.cap.release()
                    self.logger.info("Camera device released")
                except Exception as e:
                    self.logger.error(f"Error releasing camera: {e}")
                finally:
                    self.cap = None
        
        # キューをクリア
        cleared_frames = 0
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
                cleared_frames += 1
            except queue.Empty:
                break
        
        if cleared_frames > 0:
            self.logger.info(f"Cleared {cleared_frames} frames from queue")
        
        self.logger.info("Camera stopped successfully")
    
    def _initialize_camera(self) -> bool:
        """カメラの初期化"""
        try:
            # デバイスパスが存在するかチェック
            if not os.path.exists(self.config.device_path):
                self.logger.error(f"Camera device {self.config.device_path} not found")
                return False
            
            with self.lock:
                self.cap = cv2.VideoCapture(self.config.device_index)
                
                if not self.cap.isOpened():
                    self.logger.error(f"Failed to open camera {self.config.device_index}")
                    return False
                
                # カメラパラメータ設定
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
                self.cap.set(cv2.CAP_PROP_FPS, self.config.fps)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # バッファサイズを最小に
                
                # 設定値の確認
                actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
                
                self.logger.info(f"Camera initialized: {actual_width}x{actual_height} @ {actual_fps}fps")
                
                self.is_connected.set()
                self.reconnect_attempts = 0
                return True
                
        except Exception as e:
            self.logger.error(f"Camera initialization failed: {e}")
            self.stats['connection_errors'] += 1
            return False
    
    def _capture_loop(self):
        """フレームキャプチャメインループ"""
        self.logger.info("Starting capture loop")
        
        while self.is_running.is_set():
            try:
                if not self.is_connected.is_set():
                    if self.config.auto_reconnect:
                        self._attempt_reconnect()
                    else:
                        break
                    continue
                
                ret, frame = self._capture_frame()
                
                if ret:
                    self._process_frame(frame)
                else:
                    self._handle_capture_failure()
                    
            except Exception as e:
                self.logger.error(f"Unexpected error in capture loop: {e}")
                self.is_connected.clear()
                time.sleep(1)
        
        self.logger.info("Capture loop ended")
    
    def _capture_frame(self) -> tuple[bool, Optional[any]]:
        """フレームキャプチャ"""
        with self.lock:
            if not self.cap or not self.cap.isOpened():
                return False, None
            return self.cap.read()
    
    def _process_frame(self, frame):
        """フレーム処理とキューイング"""
        current_time = time.time()
        self.last_frame_time = current_time
        self.stats['frames_captured'] += 1
        
        # キューが満杯の場合、古いフレームを削除
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
                self.stats['frames_dropped'] += 1
            except queue.Empty:
                pass
        
        try:
            self.frame_queue.put_nowait((frame, current_time))
        except queue.Full:
            self.stats['frames_dropped'] += 1
    
    def _handle_capture_failure(self):
        """キャプチャ失敗時の処理"""
        self.logger.warning("Frame capture failed")
        self.is_connected.clear()
        self.stats['connection_errors'] += 1
        
        with self.lock:
            if self.cap:
                self.cap.release()
                self.cap = None
    
    def _attempt_reconnect(self):
        """再接続試行"""
        if self.reconnect_attempts >= self.config.max_reconnect_attempts:
            self.logger.error("Max reconnection attempts reached")
            self.is_running.clear()
            return
        
        self.reconnect_attempts += 1
        self.logger.info(f"Attempting to reconnect ({self.reconnect_attempts}/{self.config.max_reconnect_attempts})")
        
        time.sleep(self.config.reconnect_interval)
        
        if self._initialize_camera():
            self.logger.info("Reconnection successful")
            self.stats['last_reconnect'] = datetime.now().isoformat()
        else:
            self.logger.warning("Reconnection failed")
    
    def get_frame(self) -> Optional[tuple]:
        """最新フレームの取得"""
        try:
            return self.frame_queue.get(timeout=1.0)
        except queue.Empty:
            return None
    
    def get_status(self) -> Dict[str, Any]:
        """カメラステータス取得"""
        current_time = time.time()
        return {
            'is_running': self.is_running.is_set(),
            'is_connected': self.is_connected.is_set(),
            'queue_size': self.frame_queue.qsize(),
            'reconnect_attempts': self.reconnect_attempts,
            'stats': {
                **self.stats,
                'uptime': current_time - self.stats['uptime_start'],
                'last_frame_age': current_time - self.last_frame_time if self.last_frame_time else None
            },
            'config': asdict(self.config)
        }

# ===== シグナルハンドリング =====
class SignalHandler:
    """シグナルハンドリングクラス"""
    
    def __init__(self):
        self.shutdown_event = Event()
        self.logger = logging.getLogger(f"{__name__}.SignalHandler")
        self._camera_manager: Optional[CameraManager] = None
        self._server_handle = None
        self._cleanup_completed = False
        
    def set_camera_manager(self, camera_manager: CameraManager):
        """カメラマネージャーを設定"""
        self._camera_manager = camera_manager
        
    def set_server_handle(self, server_handle):
        """サーバーハンドルを設定"""
        self._server_handle = server_handle
    
    def setup_signal_handlers(self):
        """シグナルハンドラーのセットアップ"""
        # SIGINT (Ctrl+C)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        
        # SIGTERM (kill コマンド)
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        
        # Unix系のみのシグナル
        if hasattr(signal, 'SIGHUP'):
            # SIGHUP (設定リロード)
            signal.signal(signal.SIGHUP, self._handle_reload_signal)
            
        if hasattr(signal, 'SIGUSR1'):
            # SIGUSR1 (統計情報出力)
            signal.signal(signal.SIGUSR1, self._handle_stats_signal)
            
        if hasattr(signal, 'SIGUSR2'):
            # SIGUSR2 (カメラ再起動)
            signal.signal(signal.SIGUSR2, self._handle_camera_restart_signal)
        
        # プロセス終了時のクリーンアップ
        atexit.register(self._cleanup_on_exit)
        
        self.logger.info("Signal handlers configured")
        
    def _handle_shutdown_signal(self, signum, frame):
        """シャットダウンシグナルハンドリング"""
        signal_name = signal.Signals(signum).name
        self.logger.info(f"Received {signal_name} signal, initiating graceful shutdown...")
        
        if not self.shutdown_event.is_set():
            self.shutdown_event.set()
            self._perform_graceful_shutdown()
    
    def _handle_reload_signal(self, signum, frame):
        """設定リロードシグナルハンドリング（SIGHUP）"""
        self.logger.info("Received SIGHUP signal, reloading configuration...")
        try:
            # 設定ファイルを再読み込み
            global config_manager
            config_manager.config = config_manager.load_config()
            self.logger.info("Configuration reloaded successfully")
        except Exception as e:
            self.logger.error(f"Failed to reload configuration: {e}")
    
    def _handle_stats_signal(self, signum, frame):
        """統計情報出力シグナルハンドリング（SIGUSR1）"""
        self.logger.info("Received SIGUSR1 signal, outputting statistics...")
        if self._camera_manager:
            stats = self._camera_manager.get_status()
            self.logger.info(f"Camera Statistics: {json.dumps(stats, indent=2, default=str)}")
        else:
            self.logger.warning("Camera manager not available for statistics")
    
    def _handle_camera_restart_signal(self, signum, frame):
        """カメラ再起動シグナルハンドリング（SIGUSR2）"""
        self.logger.info("Received SIGUSR2 signal, restarting camera...")
        if self._camera_manager:
            try:
                self._camera_manager.stop()
                time.sleep(2)
                success = self._camera_manager.start()
                self.logger.info(f"Camera restart {'successful' if success else 'failed'}")
            except Exception as e:
                self.logger.error(f"Camera restart failed: {e}")
        else:
            self.logger.warning("Camera manager not available for restart")
    
    def _perform_graceful_shutdown(self):
        """グレースフルシャットダウンの実行"""
        if self._cleanup_completed:
            return
            
        self.logger.info("Starting graceful shutdown sequence...")
        
        try:
            # 1. カメラ停止
            if self._camera_manager:
                self.logger.info("Stopping camera...")
                self._camera_manager.stop()
                self.logger.info("Camera stopped")
            
            # 2. サーバー停止（uvicornプロセス）
            if self._server_handle:
                self.logger.info("Stopping server...")
                # uvicornのServerインスタンスがある場合の停止処理
                # 注意: uvicorn.run()使用時は直接制御が難しいため、プロセス終了で対応
            
            self.logger.info("Graceful shutdown completed")
            self._cleanup_completed = True
            
        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")
        finally:
            # 強制終了
            sys.exit(0)
    
    def _cleanup_on_exit(self):
        """プロセス終了時のクリーンアップ"""
        if not self._cleanup_completed:
            self.logger.info("Performing cleanup on exit...")
            if self._camera_manager:
                self._camera_manager.stop()
            self._cleanup_completed = True

# ===== グローバル変数 =====
config_manager = ConfigManager()
camera_manager: Optional[CameraManager] = None
signal_handler = SignalHandler()

# ===== FastAPI setup =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル管理"""
    global camera_manager, signal_handler
    
    # 起動時
    logging.info("Starting camera stream server...")
    
    try:
        # シグナルハンドラーの設定
        signal_handler.setup_signal_handlers()
        
        # カメラマネージャー初期化
        config = config_manager.config
        camera_manager = CameraManager(config.camera)
        signal_handler.set_camera_manager(camera_manager)
        
        # カメラ開始
        if not camera_manager.start():
            logging.error("Failed to start camera, but continuing with server startup")
        else:
            logging.info("Camera started successfully")
        
        logging.info("Server startup completed")
        
    except Exception as e:
        logging.error(f"Error during server startup: {e}")
        # 起動時エラーでもサーバーは継続（カメラなしでも管理機能は提供）
    
    yield
    
    # 終了時
    logging.info("Initiating server shutdown...")
    
    try:
        if camera_manager:
            camera_manager.stop()
        logging.info("Server shutdown completed")
        
    except Exception as e:
        logging.error(f"Error during server shutdown: {e}")
    
    finally:
        # 確実にリソースを解放
        cv2.destroyAllWindows()  # OpenCVウィンドウのクリーンアップ
        logging.info("Resource cleanup completed")

app = FastAPI(
    title="Production USB Camera Stream",
    description="High-reliability USB camera streaming service",
    version="2.0.0",
    lifespan=lifespan
)

# ミドルウェア設定
config = config_manager.config
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=config.server.trusted_hosts
)

# ===== 依存関数 =====
def get_camera_manager() -> CameraManager:
    """カメラマネージャー取得"""
    if not camera_manager:
        raise HTTPException(status_code=503, detail="Camera manager not initialized")
    return camera_manager

# ===== フレーム生成 =====
def generate_frames(camera: CameraManager):
    """フレーム生成ジェネレーター"""
    max_frame_age = config_manager.config.server.max_frame_age
    
    while True:
        try:
            frame_data = camera.get_frame()
            
            if frame_data is None:
                # カメラが利用できない場合のプレースホルダー
                yield b'--frame\r\n'
                yield b'Content-Type: text/plain\r\n\r\n'
                yield b'Camera not available\r\n'
                time.sleep(0.1)
                continue
            
            frame, timestamp = frame_data
            
            # フレームの新鮮度チェック
            if time.time() - timestamp > max_frame_age:
                continue
            
            # JPEGエンコード
            ret, buffer = cv2.imencode(
                '.jpg', 
                frame, 
                [cv2.IMWRITE_JPEG_QUALITY, camera.config.jpeg_quality]
            )
            
            if ret:
                frame_bytes = buffer.tobytes()
                yield b'--frame\r\n'
                yield b'Content-Type: image/jpeg\r\n\r\n'
                yield frame_bytes
                yield b'\r\n'
            
        except Exception as e:
            logging.error(f"Error generating frame: {e}")
            yield b'--frame\r\n'
            yield b'Content-Type: text/plain\r\n\r\n'
            yield f'Error: {str(e)}\r\n'.encode()
            time.sleep(0.1)

# ===== APIエンドポイント =====
@app.get("/", response_class=HTMLResponse)
async def index():
    """メインページ"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Production Camera Stream</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * { box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0; padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
            }
            .container {
                max-width: 1200px; margin: 0 auto;
                background: white; border-radius: 15px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                overflow: hidden;
            }
            .header {
                background: linear-gradient(135deg, #2196F3, #21CBF3);
                color: white; padding: 30px;
                text-align: center;
            }
            .header h1 { margin: 0; font-size: 2.5em; }
            .header p { margin: 10px 0 0 0; opacity: 0.9; }
            .content { padding: 30px; }
            .video-container {
                text-align: center; margin: 20px 0;
                background: #f8f9fa; border-radius: 10px;
                padding: 20px;
            }
            #videoStream {
                max-width: 100%; border-radius: 10px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            }
            .status-panel {
                display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px; margin: 30px 0;
            }
            .status-card {
                padding: 20px; border-radius: 10px;
                text-align: center; color: white;
            }
            .status-online { background: linear-gradient(135deg, #4CAF50, #45a049); }
            .status-offline { background: linear-gradient(135deg, #f44336, #da190b); }
            .status-warning { background: linear-gradient(135deg, #ff9800, #f57c00); }
            .controls {
                display: flex; justify-content: center; gap: 15px;
                flex-wrap: wrap; margin: 30px 0;
            }
            button {
                padding: 12px 24px; border: none; border-radius: 25px;
                font-size: 16px; cursor: pointer;
                transition: all 0.3s ease;
                font-weight: 500;
            }
            .btn-primary {
                background: linear-gradient(135deg, #2196F3, #21CBF3);
                color: white;
            }
            .btn-primary:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(33, 150, 243, 0.4);
            }
            .stats-grid {
                display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px; margin: 30px 0;
            }
            .stat-item {
                background: #f8f9fa; padding: 20px; border-radius: 10px;
                border-left: 4px solid #2196F3;
            }
            .stat-value { font-size: 1.5em; font-weight: bold; color: #2196F3; }
            .stat-label { color: #666; margin-top: 5px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎥 Production Camera Stream</h1>
                <p>High-reliability USB camera streaming service</p>
            </div>
            
            <div class="content">
                <div id="statusPanel" class="status-panel">
                    <div id="cameraStatus" class="status-card status-offline">
                        <div>📹 Camera Status</div>
                        <div id="cameraStatusText">Checking...</div>
                    </div>
                    <div id="connectionStatus" class="status-card status-offline">
                        <div>🔗 Connection</div>
                        <div id="connectionStatusText">Checking...</div>
                    </div>
                </div>
                
                <div class="video-container">
                    <img id="videoStream" src="/video_feed" alt="Camera Feed">
                </div>
                
                <div class="controls">
                    <button class="btn-primary" onclick="refreshStream()">🔄 Refresh</button>
                    <button class="btn-primary" onclick="toggleFullscreen()">🔍 Fullscreen</button>
                    <button class="btn-primary" onclick="downloadSnapshot()">📷 Snapshot</button>
                    <button class="btn-primary" onclick="toggleStats()">📊 Stats</button>
                </div>
                
                <div id="statsSection" class="stats-grid" style="display: none;">
                    <div class="stat-item">
                        <div id="framesCaptures" class="stat-value">-</div>
                        <div class="stat-label">Frames Captured</div>
                    </div>
                    <div class="stat-item">
                        <div id="framesDropped" class="stat-value">-</div>
                        <div class="stat-label">Frames Dropped</div>
                    </div>
                    <div class="stat-item">
                        <div id="uptime" class="stat-value">-</div>
                        <div class="stat-label">Uptime (hours)</div>
                    </div>
                    <div class="stat-item">
                        <div id="queueSize" class="stat-value">-</div>
                        <div class="stat-label">Queue Size</div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let statsVisible = false;
            
            async function checkStatus() {
                try {
                    const response = await fetch('/status');
                    const status = await response.json();
                    
                    const cameraCard = document.getElementById('cameraStatus');
                    const cameraText = document.getElementById('cameraStatusText');
                    const connCard = document.getElementById('connectionStatus');
                    const connText = document.getElementById('connectionStatusText');
                    
                    if (status.is_running && status.is_connected) {
                        cameraCard.className = 'status-card status-online';
                        cameraText.textContent = 'Online & Recording';
                        connCard.className = 'status-card status-online';
                        connText.textContent = 'Connected';
                    } else if (status.is_running) {
                        cameraCard.className = 'status-card status-warning';
                        cameraText.textContent = 'Running (No Signal)';
                        connCard.className = 'status-card status-warning';
                        connText.textContent = 'Reconnecting...';
                    } else {
                        cameraCard.className = 'status-card status-offline';
                        cameraText.textContent = 'Offline';
                        connCard.className = 'status-card status-offline';
                        connText.textContent = 'Disconnected';
                    }
                    
                    // 統計情報更新
                    if (statsVisible && status.stats) {
                        document.getElementById('framesCaptures').textContent = status.stats.frames_captured;
                        document.getElementById('framesDropped').textContent = status.stats.frames_dropped;
                        document.getElementById('uptime').textContent = Math.floor(status.stats.uptime / 3600);
                        document.getElementById('queueSize').textContent = status.queue_size;
                    }
                    
                } catch (error) {
                    console.error('Status check failed:', error);
                }
            }
            
            function refreshStream() {
                const img = document.getElementById('videoStream');
                const src = img.src.split('?')[0];
                img.src = src + '?t=' + new Date().getTime();
            }
            
            function toggleFullscreen() {
                const img = document.getElementById('videoStream');
                if (img.requestFullscreen) {
                    img.requestFullscreen();
                }
            }
            
            function downloadSnapshot() {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                const img = document.getElementById('videoStream');
                
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                ctx.drawImage(img, 0, 0);
                
                const link = document.createElement('a');
                link.download = `snapshot_${new Date().toISOString()}.jpg`;
                link.href = canvas.toDataURL();
                link.click();
            }
            
            function toggleStats() {
                const statsSection = document.getElementById('statsSection');
                statsVisible = !statsVisible;
                statsSection.style.display = statsVisible ? 'grid' : 'none';
            }
            
            // 定期的なステータスチェック
            setInterval(checkStatus, 2000);
            checkStatus();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/video_feed")
async def video_feed(camera: CameraManager = Depends(get_camera_manager)):
    """ビデオストリーミングエンドポイント"""
    if not camera.is_running.is_set():
        raise HTTPException(status_code=503, detail="Camera service not running")
    
    return StreamingResponse(
        generate_frames(camera),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/status")
async def get_status(camera: CameraManager = Depends(get_camera_manager)):
    """システム状態取得"""
    return JSONResponse(camera.get_status())

@app.get("/health")
async def health_check():
    """ヘルスチェックエンドポイント"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/camera/restart")
async def restart_camera(camera: CameraManager = Depends(get_camera_manager)):
    """カメラ再起動"""
    try:
        logging.info("API camera restart requested")
        camera.stop()
        time.sleep(2)  # デバイスの安定化待機
        success = camera.start()
        
        message = "Camera restart successful" if success else "Camera restart failed"
        logging.info(message)
        
        return {"success": success, "message": message}
        
    except Exception as e:
        error_msg = f"Camera restart failed: {str(e)}"
        logging.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/server/reload-config")
async def reload_config():
    """設定ファイル再読み込み"""
    try:
        logging.info("Configuration reload requested")
        global config_manager
        old_config = config_manager.config
        config_manager.config = config_manager.load_config()
        
        # 設定変更をログに記録
        if old_config != config_manager.config:
            logging.info("Configuration updated successfully")
        else:
            logging.info("No configuration changes detected")
        
        return {
            "success": True, 
            "message": "Configuration reloaded",
            "restart_required": "Camera restart may be required for some changes"
        }
        
    except Exception as e:
        error_msg = f"Configuration reload failed: {str(e)}"
        logging.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/server/shutdown")
async def graceful_shutdown():
    """グレースフルシャットダウン"""
    logging.info("Graceful shutdown requested via API")
    
    # 非同期でシャットダウンを実行
    def delayed_shutdown():
        time.sleep(1)  # レスポンスを返すための短い遅延
        signal_handler._perform_graceful_shutdown()
    
    Thread(target=delayed_shutdown, daemon=True).start()
    
    return {"message": "Graceful shutdown initiated"}

@app.get("/server/pid")
async def get_process_info():
    """プロセス情報取得"""
    return {
        "pid": os.getpid(),
        "signals": {
            "SIGINT": "Graceful shutdown (Ctrl+C)",
            "SIGTERM": "Graceful shutdown (kill)",
            "SIGHUP": "Reload configuration (kill -HUP)",
            "SIGUSR1": "Output statistics (kill -USR1)",
            "SIGUSR2": "Restart camera (kill -USR2)"
        } if os.name != 'nt' else {
            "SIGINT": "Graceful shutdown (Ctrl+C)",
            "SIGTERM": "Graceful shutdown"
        }
    }

@app.get("/config")
async def get_config():
    """現在の設定取得"""
    return JSONResponse(asdict(config_manager.config))

@app.put("/config")
async def update_config(new_config: dict):
    """設定更新"""
    try:
        # 設定検証とマージ
        current = asdict(config_manager.config)
        
        # ネストした辞書をマージ
        for key, value in new_config.items():
            if key in current and isinstance(current[key], dict) and isinstance(value, dict):
                current[key].update(value)
            else:
                current[key] = value
        
        # 新しい設定でConfigオブジェクト作成
        updated_config = AppConfig(
            camera=CameraConfig(**current['camera']),
            server=ServerConfig(**current['server'])
        )
        
        # 設定保存
        config_manager.save_config(updated_config)
        config_manager.config = updated_config
        
        return {"success": True, "message": "Configuration updated (restart required for some changes)"}
        
    except Exception as e:
        logging.error(f"Config update failed: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {str(e)}")

# ===== メイン実行 =====
if __name__ == "__main__":
    # 設定読み込み
    config = config_manager.config
    
    # ロギング設定
    setup_logging(config.server.log_level, config.server.log_file)
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Production USB Camera Stream Server v2.0")
    logger.info("=" * 60)
    logger.info(f"Process ID: {os.getpid()}")
    logger.info(f"Camera device: {config.camera.device_path}")
    logger.info(f"Resolution: {config.camera.width}x{config.camera.height}")
    logger.info(f"Server: http://{config.server.host}:{config.server.port}")
    logger.info(f"Log level: {config.server.log_level}")
    if config.server.log_file:
        logger.info(f"Log file: {config.server.log_file}")
    
    # シグナル情報を表示
    if os.name != 'nt':  # Unix系システム
        logger.info("")
        logger.info("Signal handling:")
        logger.info("  SIGINT/Ctrl+C  : Graceful shutdown")
        logger.info("  SIGTERM        : Graceful shutdown")
        logger.info("  SIGHUP         : Reload configuration")
        logger.info("  SIGUSR1        : Output statistics")
        logger.info("  SIGUSR2        : Restart camera")
        logger.info("")
        logger.info("Usage examples:")
        logger.info(f"  kill -HUP {os.getpid()}   # Reload config")
        logger.info(f"  kill -USR1 {os.getpid()}  # Show stats")
        logger.info(f"  kill -USR2 {os.getpid()}  # Restart camera")
    else:
        logger.info("")
        logger.info("Signal handling:")
        logger.info("  Ctrl+C         : Graceful shutdown")
    
    logger.info("=" * 60)
    
    try:
        # サーバー起動
        logger.info("Starting server...")
        
        # UvicornのServerクラスを使用してより詳細な制御
        server_config = uvicorn.Config(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=config.server.log_level.lower(),
            access_log=True,
            use_colors=True,
            server_header=False,  # Serverヘッダーを無効化
            date_header=False     # Dateヘッダーを無効化
        )
        
        server = uvicorn.Server(server_config)
        
        # シグナルハンドラーにサーバー参照を設定
        signal_handler.set_server_handle(server)
        
        # サーバー開始
        server.run()
        
    except KeyboardInterrupt:
        logger.info("Server stopped by user (KeyboardInterrupt)")
    except SystemExit as e:
        logger.info(f"Server stopped with exit code: {e.code}")
    except Exception as e:
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)
    finally:
        logger.info("Server process terminated")
        # 最終的なクリーンアップ
        if 'camera_manager' in globals() and camera_manager:
            camera_manager.stop()
        cv2.destroyAllWindows()