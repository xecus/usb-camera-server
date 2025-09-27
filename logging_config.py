import logging
from typing import Optional


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


def get_logger(name: str) -> logging.Logger:
    """ロガー取得のヘルパー関数"""
    return logging.getLogger(name)