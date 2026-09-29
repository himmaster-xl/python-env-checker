# Python 环境检查器

一个**纯 Python（零第三方依赖）**的 Windows 桌面工具，用于**查看、管理、删除**电脑上的所有 Python 环境，统计环境与包的占用大小，并检测 ML 库关联的模型文件。带卡通像素猫进度条 🐱

> 本仓库只包含**源码**。运行 `.spec` 打包后即可得到免安装的单文件 exe（详见下文「打包 exe」）。

## 功能

- **全面扫描**：官方安装版（注册表 + py 启动器 + 常见目录）、Anaconda / Miniconda 主环境与全部子环境、venv / virtualenv（含损坏的）、pyenv-win、商店版（自动跳过占位符）、PATH 与便携版
- **大小统计**：每个环境的环境总大小、包大小（site-packages）、包数量；行内容量条形图；按类型着色（官方=蓝、Conda=绿、Conda 环境=青、损坏=红）；点击列标题排序，顶部筛选
- **详情卡片**：选中环境后底部大字号显示名称、路径、环境总大小、包大小、包数量与快捷操作按钮
- **包管理**：双击环境查看每个包的名称、版本、占用大小（依据 RECORD 安装清单精确统计，不启动目标解释器），支持搜索与 pip 一键卸载
- **模型文件检测**：检测已安装 ML 库（transformers / torch / paddle / whisper / keras / nltk / modelscope / kagglehub / gdown / ultralytics 等）的官方模型缓存（如 HuggingFace 的 `models--*` 目录），以及环境内 ≥50MB 的模型权重文件（.pt/.pth/.onnx/.safetensors 等，标注"慎删"），多选删除带确认
- **删除环境**：二次确认；自动处理只读文件与超长路径；拒绝删除正在运行本程序的环境
- **深度清理（安全增强）**：删除官方安装版 / Conda 环境时可选**同时清理指向该环境的注册表键和开始菜单快捷方式**，并自动清理 `environments.txt` 死条目。安全机制：
  - 只清理**精确指向该环境目录**的条目（带分隔符匹配，`miniconda3` 不会误伤 `miniconda3-env`）
  - 注册表键删除前自动 `reg export` 备份到 `~/.py_env_checker_backup/<时间戳>/`，双击 `.reg` 可还原
  - 需要管理员权限的位置无权限时自动跳过并报告，不影响其余清理
  - 只有在环境目录**确认已删除之后**才执行残留清理
- **缓存清理中心**：一键回收 pip 下载缓存（`%LOCALAPPDATA%\pip\cache`）和 Conda 包缓存（`pkgs`，仅限含 `conda-meta` 的主环境），带真实进度显示（像素猫 🐱 的位置 = 总完成度）
- **其他**：右键菜单打开环境所在文件夹；官方安装版可一键启动注册表中的官方卸载程序

## 运行

需要 Windows 10/11 + Python 3.8+（无任何第三方依赖）：

```
python main.py
```

命令行自检（打印扫描结果，不进界面）：

```
python main.py --selftest
```

## 打包 exe

```
pip install pyinstaller
python -m PyInstaller --noconfirm "Python环境检查器.spec"
```

产物：`dist\Python环境检查器.exe`（单文件免安装，约 12 MB）。

重新生成图标（GDI+ 绘制的卡通齿轮，多尺寸 .ico）：

```
powershell -ExecutionPolicy Bypass -File _make_icon.ps1
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `main.py` | 图形界面（Tkinter）与入口 |
| `scanner.py` | 环境发现与版本识别 |
| `sizes.py` | 目录大小统计与格式化（支持长路径） |
| `packages.py` | 包清单与每个包的大小（RECORD 解析） |
| `models.py` | 模型文件检测（ML 库缓存 + 环境内大模型文件） |
| `remover.py` | 环境删除、pip 卸载、查找官方卸载程序 |
| `cleaner.py` | 深度清理：注册表/快捷方式/environments.txt/缓存（含备份） |
| `app.ico` | 卡通齿轮图标（多尺寸） |
| `_make_icon.ps1` | 图标生成脚本 |
| `Python环境检查器.spec` | PyInstaller 打包配置 |
| `run.bat` | 源码方式启动 |

## 安全说明

- 删除环境是**永久删除**（不进回收站），程序会拒绝删除正在运行本程序的环境
- 删除"官方安装"版 Python 建议优先使用右键的"打开官方卸载程序"
- 商店版 Python 引导到系统"应用"设置页卸载
- Conda 主环境的"环境总大小"包含各环境共享的 `pkgs` 包缓存，数字偏大属正常现象
- 程序异常时日志在 `%TEMP%\python环境检查器.log`
