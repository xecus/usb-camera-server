import json
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

from logging_config import get_logger


@dataclass
class CameraConfig:
    """カメラ設定"""
    device_index: int = 0
    device_path: str = "/dev/video0" if os.name != 'nt' else None
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

    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = ["*"]
        if self.trusted_hosts is None:
            self.trusted_hosts = ["*"]


@dataclass
class AppConfig:
    """アプリケーション設定"""
    camera: CameraConfig
    server: ServerConfig


class ConfigManager:
    """設定ファイル管理"""

    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.logger = get_logger(f"{__name__}.ConfigManager")
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
                self.logger.warning(f"Failed to load config from {self.config_path}: {e}")
                self.logger.info("Using default configuration")

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
            self.logger.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")

    def reload_config(self) -> AppConfig:
        """設定ファイルの再読み込み"""
        old_config = self.config
        self.config = self.load_config()

        if old_config != self.config:
            self.logger.info("Configuration updated successfully")
        else:
            self.logger.info("No configuration changes detected")

        return self.config

    def update_config(self, new_config_data: dict) -> AppConfig:
        """設定の更新"""
        # 設定検証とマージ
        current = asdict(self.config)

        # ネストした辞書をマージ
        for key, value in new_config_data.items():
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
        self.save_config(updated_config)
        self.config = updated_config

        return self.config