# 依赖包安装说明

本文档说明 `蓝点UWB-TWR 上位机V1.0` 在 Windows、Linux 和 macOS 上的 Python 运行依赖，以及如何通过依赖清单快速完成安装。

## 1. 运行环境

推荐环境：

- Python `3.9` 到 `3.11`
- Windows 10/11、Ubuntu/Debian Linux、macOS Intel 或 Apple Silicon

兼容环境：

- 当前工程代码兼容 Python `3.7+`
- 如果继续使用 Python 3.7，`requirements.txt` 会通过版本条件安装兼容的 NumPy 版本

## 2. 第三方依赖

完整依赖清单见：

```text
requirements.txt
```

依赖说明：

| 依赖包 | 用途 |
|---|---|
| `PyQt5` | GUI 界面、表格、绘图视图、信号槽 |
| `numpy` | 线性最小二乘、NLLS、EKF 矩阵计算 |
| `pyserial` | 串口 COM 通信和串口枚举 |

以下模块来自 Python 标准库，不需要额外安装：

```text
argparse, contextlib, csv, ctypes, datetime, io, ipaddress, logging,
math, os, random, re, socket, struct, sys, threading, time
```

## 3. Windows 安装

在工程目录执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

验证依赖：

```powershell
python -c "import PyQt5, numpy, serial; print('dependencies ok')"
```

启动软件：

```powershell
pythonw .\UWB_Location_Tool.pyw
```

## 4. Linux 安装

Ubuntu/Debian 建议先安装 Qt 运行时常用系统库：

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0 libgl1
```

然后在工程目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

验证依赖：

```bash
python -c "import PyQt5, numpy, serial; print('dependencies ok')"
```

如果需要访问 USB 串口，通常需要把当前用户加入串口权限组：

```bash
sudo usermod -aG dialout "$USER"
```

执行后重新登录系统再使用串口。

启动软件：

```bash
python UWB_Location_Tool.pyw
```

## 5. macOS 安装

在工程目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

验证依赖：

```bash
python -c "import PyQt5, numpy, serial; print('dependencies ok')"
```

macOS 串口通常显示为：

```text
/dev/cu.usbserial-*
/dev/cu.wchusbserial*
/dev/cu.usbmodem*
```

启动软件：

```bash
python UWB_Location_Tool.pyw
```

## 6. 离线安装

有网络的电脑先下载 wheel 包：

```bash
python -m pip download -r requirements.txt -d wheelhouse
```

把 `wheelhouse` 目录和工程一起拷贝到目标电脑后执行：

```bash
python -m pip install --no-index --find-links wheelhouse -r requirements.txt
```

## 7. 常见安装问题

### 7.1 PyQt5 安装失败

建议：

- 使用 Python `3.9` 到 `3.11`。
- 先升级 pip：`python -m pip install --upgrade pip setuptools wheel`。
- Linux 上确认 Qt 相关系统库已安装。

### 7.2 Linux 启动时报 xcb 错误

通常是系统缺少 Qt xcb 依赖，Ubuntu/Debian 可执行：

```bash
sudo apt install -y libxcb-xinerama0 libxcb-cursor0 libxkbcommon-x11-0 libgl1
```

### 7.3 找不到串口

建议：

- Windows：在设备管理器确认 COM 号和 USB 转串口驱动。
- Linux：确认用户已加入 `dialout` 组。
- macOS：优先选择 `/dev/cu.*` 设备。

### 7.4 依赖版本说明

`requirements.txt` 使用 NumPy 版本条件：

- Python 3.7 安装 `numpy 1.21.x`
- Python 3.8 安装 `numpy 1.24.x`
- Python 3.9 及以上安装 `numpy 1.26.x`

这样可以兼顾旧 Python 环境和新系统上的安装成功率。
