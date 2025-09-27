import cv2
import os
import time
import queue
from threading import Thread, Event, Lock
from typing import Optional, Dict, Any
from dataclasses import asdict

from config import CameraConfig
from logging_config import get_logger


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

        self.logger = get_logger(f"{__name__}.CameraManager")

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
            # デバイスパスが存在するかチェック（Linux/macOSのみ）
            if self.config.device_path and not os.path.exists(self.config.device_path):
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
            from datetime import datetime
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