import signal
import sys
import time
import json
import atexit
from threading import Event
from typing import Optional

from camera import CameraManager
from logging_config import get_logger


class SignalHandler:
    """シグナルハンドリングクラス"""

    def __init__(self):
        self.shutdown_event = Event()
        self.logger = get_logger(f"{__name__}.SignalHandler")
        self._camera_manager: Optional[CameraManager] = None
        self._config_manager = None
        self._server_handle = None
        self._cleanup_completed = False

    def set_camera_manager(self, camera_manager: CameraManager):
        """カメラマネージャーを設定"""
        self._camera_manager = camera_manager

    def set_config_manager(self, config_manager):
        """設定マネージャーを設定"""
        self._config_manager = config_manager

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
            if self._config_manager:
                self._config_manager.reload_config()
                self.logger.info("Configuration reloaded successfully")
            else:
                self.logger.warning("Config manager not available for reload")
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