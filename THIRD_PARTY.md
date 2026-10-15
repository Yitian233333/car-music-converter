# 开源来源和构建信息

本应用没有调用任何音乐平台在线服务，也不包含账户信息或私人密钥数据库。

KRC 解码根据公开格式信息独立实现：固定 XOR 后进行 zlib 解压。格式与常量交叉参考 https://github.com/kangkang520/kugou-lyric/blob/master/src/util.ts 和 https://github.com/kangkang520/kugou-lyric/blob/master/src/consts.ts 。未复制其联网请求、搜索、解析器代码或歌词样本。运行时使用 Python 标准库 zlib / JSON / Base64；不访问歌词平台服务。

| 组件 | 来源 | 许可 |
|---|---|---|
| KGM v3/v5、PC 酷狗数据库、TEA / QMC2 与独立测试向量 | Unlock Music CLI v0.2.12，https://proxy.golang.org/unlock-music.dev/cli/@v/v0.2.12.zip | MIT，见 `licenses/Unlock-Music-MIT.txt` |
| 旧版 KGM 掩码常量 | https://github.com/onavcn/kugou-audio-unlock | MIT，见 `licenses/kugou-audio-unlock-MIT.txt` |
| 现代酷狗格式分派与兼容性研究参考 | https://github.com/Aaron-gx/KGG-Decryptor | MIT，见 `licenses/KGG-Decryptor-MIT.txt` |
| QMC2 / 数据库交叉校对参考 | https://github.com/skxxxkx666/Kugo-Music-Converter | GPL v3，应用包含 GPL v3 许可全文 |
| PySide6 Essentials / Shiboken6 6.12.0，Qt 6.12 | https://code.qt.io/cgit/pyside/pyside-setup.git/ ; https://code.qt.io/cgit/qt/ | LGPL v3 / GPL，动态链接，见 `licenses/LGPL-3.0.txt` |
| NumPy 2.5.4 及随附组件 | https://github.com/numpy/numpy | BSD 及其组件许可，见 `licenses/NumPy.txt` |
| PyCryptodome 4.0.0 | https://github.com/Legrandin/pycryptodome | BSD / Public Domain，见 `licenses/PyCryptodome.txt` |
| Python 3.13 | https://www.python.org/downloads/source/ | PSF，见 `licenses/Python.txt` |
| PyInstaller 6.22.3 启动器 | https://github.com/pyinstaller/pyinstaller | GPL v2+ with bootloader exception，见 `licenses/PyInstaller.txt` |
| FFmpeg 7.1，由 imageio-ffmpeg 0.6.0 提供 Windows x86_64 二进制 | https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0 ; https://www.gyan.dev/ffmpeg/builds/ | 实际编译配置见 `licenses/FFmpeg-build.txt`；GPL v3 全文见 `LICENSE` |

本次构建所用 FFmpeg 可执行文件的 SHA-256、版本和实际 `-L` 输出记录在 `licenses/FFmpeg-build.txt`。FFmpeg 对应 7.1 源码：https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz 。其 imageio 构建脚本与分发信息：https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0 。本项目通过独立进程调用 FFmpeg。未修改 Qt、Python、NumPy 或上述第三方二进制。

源码可直接在项目根目录审阅；便携目录中附有应用源码 ZIP（不包含个人音频、数据库或本机报告）。所有来源的声明均保留在便携程序的 `licenses` 目录内。
