# Windows EXE 打包说明

本文档说明如何把 `蓝点UWB-TWR 上位机V1.0` 打包成无需目标电脑安装 Python 依赖的 Windows 可执行程序。

## 1. 目标

打包输出：

```text
dist/Landian_UWB_TWR_Host_V1.0.exe
```

目标电脑使用方式：

1. 复制 `Landian_UWB_TWR_Host_V1.0.exe` 到目标 Windows 电脑。
2. 双击 `Landian_UWB_TWR_Host_V1.0.exe`。

目标电脑不需要安装 Python、PyQt5、numpy、pyserial。

## 2. 兼容性策略

为了兼容 Windows 7 / Windows 10 / Windows 11：

- 推荐在 Windows 上使用 `64bit Python 3.7.x` 打包。
- 使用 `PyInstaller 5.13.2`。
- 使用单文件 `onefile` 模式，最终只交付一个 exe。
- 使用 `windowed` 模式，启动时不显示 Python terminal。

说明：

- Python 3.7 是兼容 Windows 7 的稳妥选择。
- 单文件版启动时会先解压运行时文件，首次启动会比单目录版慢一些，但交付最简单。
- 如果需要支持 32 位 Windows 7，需要额外使用 32 位 Python 3.7 重新打包一份 32 位版本。

## 3. 打包机准备

在打包电脑上进入工程目录，执行：

```powershell
py -3.7 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-build-windows.txt
```

如果当前电脑默认 `python` 已经是 Python 3.7，也可以直接使用当前环境。

## 4. 一键打包

PowerShell：

```powershell
.\scripts\build_windows_exe.ps1
```

或使用批处理：

```bat
scripts\build_windows_exe.bat
```

跳过依赖安装，只重新打包：

```powershell
.\scripts\build_windows_exe.ps1 -SkipInstall
```

## 5. 打包配置文件

PyInstaller 配置文件：

```text
packaging/landian_uwb_twr.spec
```

关键配置：

- 入口：`UWB_Location_Tool.pyw`
- 输出名：`Landian_UWB_TWR_Host_V1.0`
- 图标：`uwb_location.ico`
- 模式：`onefile`
- 模式：`console=False`
- 数据文件：打包 `uwb_location.ico`
- 隐藏导入：收集 `serial.tools` 子模块，保证串口枚举可用

## 6. 发布前检查

在打包机上检查：

```powershell
.\dist\Landian_UWB_TWR_Host_V1.0.exe
```

建议验证：

- 软件能正常启动，没有 terminal 弹窗。
- 标题为 `蓝点UWB-TWR 上位机V1.0`。
- 窗口和任务栏图标正常。
- 默认通信 Tab 为 `COM`。
- TCP 默认端口为 `8888`。
- 串口列表可刷新。
- 调试窗口日志显示不会把完整行拆成多行。

## 7. 发布内容

交付给用户时，推荐交付：

```text
dist/Landian_UWB_TWR_Host_V1.0.exe
```

该文件已经包含 Qt 插件、Python 运行库和算法依赖库，用户只需要双击这个 exe。

## 8. 常见问题

### 8.1 目标电脑提示缺少系统 DLL

Windows 7 电脑建议安装：

- Windows 7 SP1
- Microsoft Visual C++ 2015-2022 Redistributable

如果必须覆盖非常老的 Win7 环境，建议在干净 Win7 SP1 虚拟机内做一次真实启动测试。

### 8.2 杀毒软件误报

建议：

- 使用当前单文件模式交付时，建议对 exe 做企业签名或内部白名单。
- 不使用 UPX 压缩。
