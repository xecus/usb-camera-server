# USB Camera Server

Production-ready USB camera streaming server with web interface and management APIs.

## 🎯 Overview

A high-reliability USB camera streaming service built with Python, FastAPI, and OpenCV. Designed for production environments requiring stable, continuous camera streaming with comprehensive monitoring and management capabilities.

## ✨ Features

### Core Functionality
- 📹 **Real-time USB Camera Streaming** - MJPEG video streaming over HTTP
- 🌐 **Web Interface** - Modern, responsive web UI with live controls
- 🔄 **Auto-reconnection** - Automatic camera reconnection with configurable retry logic
- 📊 **System Monitoring** - Real-time statistics and health monitoring
- ⚙️ **Configuration Management** - JSON-based configuration with hot reload

### Management & Control
- 🔌 **RESTful API** - Complete management API for camera and server control
- 📡 **Signal Handling** - Unix signal support for process management
- 📝 **Comprehensive Logging** - Structured logging with file and console output
- 🛡️ **Error Recovery** - Robust error handling and recovery mechanisms
- 🔧 **Runtime Configuration** - Update settings without restart

### Production Features
- 🚀 **High Performance** - Optimized frame processing with queue buffering
- 🔒 **Security** - CORS and trusted host middleware support
- 📈 **Scalability** - Configurable frame quality and buffer management
- 🐳 **Container Ready** - Easy deployment with minimal dependencies

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- USB camera device
- Linux/macOS/Windows support

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/usb-camera-server.git
   cd usb-camera-server
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the server**
   ```bash
   python main.py
   ```

4. **Access the web interface**
   Open http://localhost:8000 in your browser

## 📋 Configuration

The server uses a JSON configuration file (`config.json`) that's automatically created with default values on first run.

### Camera Configuration
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

### Server Configuration
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

## 🔧 API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web interface |
| `GET` | `/video_feed` | MJPEG video stream |
| `GET` | `/status` | System status and statistics |
| `GET` | `/health` | Health check endpoint |

### Management Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/camera/restart` | Restart camera service |
| `POST` | `/server/reload-config` | Reload configuration |
| `POST` | `/server/shutdown` | Graceful shutdown |
| `GET` | `/server/pid` | Process information |
| `GET` | `/config` | Get current configuration |
| `PUT` | `/config` | Update configuration |

### Status Response Example
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

## 🔄 Signal Handling (Unix/Linux)

The server supports Unix signals for process management:

| Signal | Action | Description |
|--------|--------|-------------|
| `SIGINT` | Shutdown | Graceful shutdown (Ctrl+C) |
| `SIGTERM` | Shutdown | Graceful shutdown (kill) |
| `SIGHUP` | Reload | Reload configuration |
| `SIGUSR1` | Stats | Output statistics to log |
| `SIGUSR2` | Restart | Restart camera service |

### Usage Examples
```bash
# Get process ID
curl localhost:8000/server/pid

# Reload configuration
kill -HUP <pid>

# Show statistics
kill -USR1 <pid>

# Restart camera
kill -USR2 <pid>
```

## 🛠️ Development

### Project Structure
```
usb-camera-server/
├── main.py              # Main application file
├── requirements.txt     # Python dependencies
├── config.json         # Configuration file (auto-generated)
└── README.md           # This file
```

### Key Components

#### CameraManager
- Handles camera initialization and frame capture
- Implements automatic reconnection logic
- Manages frame buffering with configurable queue size
- Provides comprehensive statistics and monitoring

#### ConfigManager
- JSON-based configuration management
- Automatic default configuration generation
- Runtime configuration updates

#### SignalHandler
- Graceful shutdown handling
- Unix signal processing for management operations
- Resource cleanup and proper termination

### Development Setup

1. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   # or
   venv\Scripts\activate     # Windows
   ```

2. **Install development dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run in development mode**
   ```bash
   # With debug logging
   python main.py

   # Check logs
   tail -f camera_stream.log
   ```

### Testing Camera Setup

1. **List available cameras**
   ```bash
   # Linux
   ls /dev/video*

   # macOS
   system_profiler SPCameraDataType

   # Windows
   # Use Device Manager
   ```

2. **Test camera access**
   ```python
   import cv2
   cap = cv2.VideoCapture(0)  # Change index as needed
   ret, frame = cap.read()
   print(f"Camera accessible: {ret}")
   cap.release()
   ```

## 🐳 Docker Deployment

### Dockerfile Example
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .
EXPOSE 8000

# Ensure camera device access
RUN apt-get update && apt-get install -y \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

CMD ["python", "main.py"]
```

### Docker Run
```bash
# Build image
docker build -t usb-camera-server .

# Run with camera device access
docker run -p 8000:8000 --device=/dev/video0 usb-camera-server
```

## 🔍 Monitoring & Logging

### Log Levels
- `DEBUG`: Detailed debug information
- `INFO`: General information (default)
- `WARNING`: Warning messages
- `ERROR`: Error conditions
- `CRITICAL`: Critical errors

### Log Format
```
2024-01-15 10:30:45,123 - main.CameraManager - INFO - Camera started successfully
```

### Monitoring Endpoints
- `/health` - Basic health check
- `/status` - Detailed system status
- Real-time statistics in web interface

## 🚨 Troubleshooting

### Common Issues

**Camera not detected**
```bash
# Check device permissions
ls -l /dev/video*
# Add user to video group
sudo usermod -a -G video $USER
```

**Port already in use**
```bash
# Check what's using the port
sudo netstat -tulpn | grep :8000
# Kill process or change port in config
```

**Permission denied**
```bash
# Run with proper permissions
sudo python main.py
# Or fix device permissions
sudo chmod 666 /dev/video0
```

**High CPU usage**
- Reduce frame rate in configuration
- Lower JPEG quality setting
- Adjust buffer size

## 📈 Performance Tuning

### Optimization Tips

1. **Frame Rate**: Lower FPS for reduced CPU usage
2. **Resolution**: Use smaller resolution for better performance
3. **JPEG Quality**: Balance between quality and bandwidth
4. **Buffer Size**: Adjust based on network conditions
5. **Max Frame Age**: Configure stale frame filtering

### Resource Monitoring
```bash
# CPU and memory usage
top -p $(pgrep -f main.py)

# Network connections
ss -tulpn | grep :8000

# Disk I/O (logs)
iotop
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

### Code Style
- Follow PEP 8 guidelines
- Use type hints where possible
- Add docstrings for functions and classes
- Maintain existing error handling patterns

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🔗 Related Projects

- [OpenCV](https://opencv.org/) - Computer vision library
- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework
- [Uvicorn](https://www.uvicorn.org/) - ASGI server

## 📞 Support

- Create an issue for bug reports
- Submit feature requests via GitHub issues
- Check existing issues for solutions