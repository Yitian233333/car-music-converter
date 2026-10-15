# 随行音乐转换器

Windows 10 / 11 x64 本地音乐批量转换工具，将 KGM、KGMA、KGG 和完整音频缓存转换成 MP3、FLAC，或解密后保留原始音频格式，方便车载播放。

音频和酷狗数据库在本机处理，不上传文件，不登录账号，不请求在线密钥。请仅处理自己拥有使用权的音频。

## 下载与使用

从 [Releases](https://github.com/Yitian233333/car-music-converter/releases) 下载 `随行音乐转换器-Windows-x64.zip`，完整解压后打开 `CarMusic.exe`。无需安装 Python 或 FFmpeg；请保留整个程序文件夹及 `_internal` 目录。

1. 添加文件、文件夹，或点击“扫描酷狗缓存”。也支持拖入文件和文件夹。
2. 选择 MP3、FLAC 或“原始格式”，设置输出目录。
3. 点击“开始批量转换”。完成后查看逐曲结果和输出目录中的 CSV 报告。

转换只读取原文件。同名输出自动添加编号，不覆盖已有文件。取消后保留已完成的输出，清理当前临时文件。再次开始会重新处理队列，可能生成带编号的副本。

## 功能

- 批量扫描、去重、转换和取消，识别有扩展名文件及部分无扩展名完整缓存。
- MP3 提供 128 / 192 / 256 / 320 kbps CBR；FLAC 保留或编码音频；原始格式模式不重新编码。
- 车载兼容模式输出 44.1 kHz、双声道，FLAC 限制为 16 bit。高解析音源会降至 CD 规格。
- 自动查找本机 `KGMusicV3.db`，也可手动选择数据库；访问受限的候选路径会提示并跳过。
- 解密后检查文件头，编码后完整解码验证，通过检查才保存最终文件。
- 图形界面与命令行共享转换实现，提供 CSV 结果报告。

## 格式支持与实测范围

| 输入 | 处理方式 | 验证情况 |
|---|---|---|
| KGM v3 | 文件内密钥，流式解密 | 12 首真实下载文件全部通过；另有 66 个历史缓存中 64 个通过、2 个完整解码失败 |
| KGG v5 | 本机数据库逐曲 eKey、TEA、QMC2 MAP / RC4 | 240 首真实下载文件全部通过 |
| KGMA、其他 v3 / v5 封装 | 根据文件头选择相同版本的处理逻辑 | 已有实现和构造案例验证，缺少真实 KGMA 样本 |
| 旧版 KGM / KGMA v2、兼容掩码 v4 / VPR | 公开掩码算法，解密结果必须通过完整解码 | 未获得对应真实版本样本 |
| 无扩展名缓存 | 检查文件头；完整标准音频或支持的加密流可尝试处理 | 分片、未知封装和缺少密钥的缓存不保证可转换 |
| MP3 / FLAC / WAV / OGG / M4A / AAC / WMA | FFmpeg 标准音频转换 | WAV、MP3、FLAC 已验证 |

不能保证所有历史格式、损坏缓存或后续版本都能转换。缺少密钥时，可在自己已获授权的酷狗客户端中完整播放或重新下载，再点击“重新扫描密钥库”；客户端不一定会写入所需密钥。

**有损音频转成 FLAC 不会恢复无损音质。** 本次真实样本解密后为 12 个 MP3、240 个 OGG，没有发现原生 FLAC。选择“原始格式”可避免重新编码；选择 MP3 会按所选码率重新编码。

车机对 FLAC、U 盘文件系统和文件名的支持取决于具体车型，建议先用少量输出试听。

## 已完成的验证

版本 `1.0.1`，验证日期：2026-10-15。

- 252 首真实下载文件成功转换为 320 kbps、44.1 kHz、双声道 MP3，逐首完整解码通过，252 个原文件 SHA-256 均未改变。
- 便携 EXE 对真实 KGM / KGG 分别测试 MP3 和 FLAC，共 4 个案例全部通过。抽测输出与解密后音频的时长差不超过 0.03 秒。
- 便携 EXE 的两份 MP3 输出与同参数批量转换结果字节一致。
- 实际桌面窗口确认版本显示正常、252 首文件导入成功、240 条歌曲密钥读取正常。
- 数据库访问受限、无数据库转换 KGM v3、KGG 缺少密钥的异常处理检查通过。
- 便携压缩包和源码压缩包完整性、SHA-256 校验通过。

完整解码检查可以发现解码错误，不能证明歌曲内容、时长与官方发行版本完全一致。仓库和发布包不包含私人音乐、客户端数据库、逐曲密钥、设置或私人路径报告。

## 命令行

`CarMusicCLI.exe` 与桌面版共享 `_internal`，请放在同一目录。

```powershell
.\CarMusicCLI.exe --cli 'D:\MyMusic' --format mp3 --bitrate 320 --car --output 'D:\CarMusic'
.\CarMusicCLI.exe --cli 'D:\MyMusic' --format flac --output 'D:\FLAC'
.\CarMusicCLI.exe --cli --scan-kugou --format original --output 'D:\OriginalAudio'
.\CarMusicCLI.exe --cli 'D:\MyMusic\song.kgg' --database 'D:\LocalData\KGMusicV3.db' --output 'D:\CarMusic'
```

返回值：`0` 全部成功，`1` 存在失败，`2` 没有发现文件或参数错误，`130` 键盘取消。CLI 默认不进行车载重采样，加 `--car` 才开启。

## 源码运行与构建

使用 Windows x64 和 Python 3.13：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe build_release.py
```

构建结果位于 `release`，包括便携程序文件夹、Windows ZIP、SHA-256 文件及随程序附带的源码 ZIP。FFmpeg 在构建时打包，程序运行时不下载依赖。开发环境和构建中间文件不属于程序运行依赖。

| 文件 | 用途 |
|---|---|
| `main.py` | GUI / CLI 入口 |
| `car_music/crypto.py` | 格式、数据库和加密流解析 |
| `car_music/discovery.py` | 文件及数据库发现 |
| `car_music/convert.py` | 转换、校验和同名保护 |
| `car_music/ui.py` | 桌面界面 |
| `build_release.py`、`CarMusic.spec` | 便携程序构建与打包 |

## 开源许可

项目采用 [GPL-3.0-or-later](LICENSE)。算法来源和第三方组件声明见 [THIRD_PARTY.md](THIRD_PARTY.md)，完整许可文本见 `licenses`。Qt DLL 动态链接并保留替换能力；未修改第三方运行时或二进制。
