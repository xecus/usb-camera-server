import cv2
import os
import time
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from threading import Thread

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn

from config import ConfigManager
from logging_config import setup_logging, get_logger
from camera import CameraManager
from signal_handler import SignalHandler

# ロガーの取得
logger = get_logger(__name__)


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
    logger.info("Starting camera stream server...")
    
    try:
        # シグナルハンドラーの設定
        signal_handler.setup_signal_handlers()
        signal_handler.set_config_manager(config_manager)

        # カメラマネージャー初期化
        config = config_manager.config
        camera_manager = CameraManager(config.camera)
        signal_handler.set_camera_manager(camera_manager)
        
        # カメラ開始
        if not camera_manager.start():
            logger.error("Failed to start camera, but continuing with server startup")
        else:
            logger.info("Camera started successfully")

        logger.info("Server startup completed")

    except Exception as e:
        logger.error(f"Error during server startup: {e}")
        # 起動時エラーでもサーバーは継続（カメラなしでも管理機能は提供）
    
    yield
    
    # 終了時
    logger.info("Initiating server shutdown...")
    
    try:
        if camera_manager:
            camera_manager.stop()
        logger.info("Server shutdown completed")
        
    except Exception as e:
        logger.error(f"Error during server shutdown: {e}")
    
    finally:
        # 確実にリソースを解放
        cv2.destroyAllWindows()  # OpenCVウィンドウのクリーンアップ
        logger.info("Resource cleanup completed")

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
            logger.error(f"Error generating frame: {e}")
            yield b'--frame\r\n'
            yield b'Content-Type: text/plain\r\n\r\n'
            yield f'Error: {str(e)}\r\n'.encode()
            time.sleep(0.1)

# ===== APIエンドポイント =====
@app.get("/", response_class=HTMLResponse)
async def index():
    """メインページ"""
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Template file not found")

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
        logger.info("API camera restart requested")
        camera.stop()
        time.sleep(2)  # デバイスの安定化待機
        success = camera.start()
        
        message = "Camera restart successful" if success else "Camera restart failed"
        logger.info(message)
        
        return {"success": success, "message": message}
        
    except Exception as e:
        error_msg = f"Camera restart failed: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/server/reload-config")
async def reload_config():
    """設定ファイル再読み込み"""
    try:
        logger.info("Configuration reload requested")
        global config_manager
        old_config = config_manager.config
        config_manager.reload_config()
        
        # 設定変更をログに記録
        if old_config != config_manager.config:
            logger.info("Configuration updated successfully")
        else:
            logger.info("No configuration changes detected")
        
        return {
            "success": True, 
            "message": "Configuration reloaded",
            "restart_required": "Camera restart may be required for some changes"
        }
        
    except Exception as e:
        error_msg = f"Configuration reload failed: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/server/shutdown")
async def graceful_shutdown():
    """グレースフルシャットダウン"""
    logger.info("Graceful shutdown requested via API")
    
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
    from dataclasses import asdict
    return JSONResponse(asdict(config_manager.config))

@app.put("/config")
async def update_config(new_config: dict):
    """設定更新"""
    try:
        global config_manager
        config_manager.update_config(new_config)
        return {"success": True, "message": "Configuration updated (restart required for some changes)"}

    except Exception as e:
        logger.error(f"Config update failed: {e}")
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