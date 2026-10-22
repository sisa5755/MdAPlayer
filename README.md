# 🎬 MdAPlayer

**MdAPlayer** is a modern, lightweight, and high-performance cross-platform media player built with Python (**PySide6**) and **libmpv** (backed by FFmpeg).

It combines the playback capabilities of VLC and mpv with a sleek, minimalist, and fluid user interface. Thanks to hardware acceleration, it smoothly plays HEVC (H.265), AV1, VP9, HDR, and 10-bit content with low CPU/GPU overhead.

---

## ✨ Features

* **🚀 Powerful Playback Engine:** Powered by FFmpeg/libmpv to support almost every video and audio container/format (MKV, MP4, AVI, FLAC, MP3, M3U8, etc.).
* **⚡ Hardware Acceleration:** Seamless GPU decoding via NVDEC, VAAPI, VideoToolbox, and D3D11VA.
* **🎨 Modern Glassmorphism UI:** Auto-hiding floating control bar, fluid animations, and custom styling.
* **📑 Playlist Drawer:** Easily accessible slide-out playlist panel with drag-and-drop support.
* **📌 Advanced Functionality:**
  * **2× Speed-Up on Hold:** Press and hold on the video area to play at 2× speed instantly.
  * **Bookmarks:** Save timestamped locations with notes.
  * **Night Mode:** Dynamic audio normalization powered by the `dynaudnorm` filter.
  * **Sleep Timer:** Automatically pause playback after a set time or when the current file ends.
  * **Auto-Pause:** Option to automatically pause when the application window loses focus.
  * **Media Information:** Real-time overlay and dialog for resolution, codecs, bitrate, FPS, and color profiles.

---

## 🛠️ Requirements & Installation

### 1. Pre-built Installer (.msi)
Windows users can download the compiled installer package directly:
* Go to the Releases section, download **`MdAPlayer-Setup.msi`**, and run the setup.

### 2. Running from Source

Make sure you have **Python 3.9+** installed on your system.

#### Install Python Dependencies:
```bash
pip install PySide6 python-mpv
