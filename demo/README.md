# 拼贴工作台离线演示

2026-10-13 的完整浏览快照：38 个任务、85 版成图、41 个生成视频。包含参考图、客户素材、图层图像、历史成图、已有修改记录和原 Live 视频。图片与视频按内容哈希去重后随 Git 保存，无需重新下载业务素材。

## Windows 10/11 64 位

1. 克隆下方分支，或在该分支点击 **Code → Download ZIP**，完整解压。
2. 进入 `demo` 文件夹，双击 `start.vbs`。
3. 浏览器自动打开工作台，默认进入任务 `b85eec1d0ef5933e`。
4. 使用结束后双击 `stop.vbs`。

```bash
git clone --branch demo/offline-20261013 --single-branch https://github.com/littlesun0727/collage-recreate.git
```

随包提供 Python Windows 运行时，不需要另装 Python、Node、Codex、模型或 API 密钥。如果系统禁用 VBS，在仓库根目录运行：

```powershell
.\demo\runtime\python.exe .\demo\viewer.py
```

服务只监听本机。默认使用 8795 端口，端口被占用时会自动选择空闲端口；实际地址写入 `demo/viewer-url.txt`。浏览器未自动打开时，复制该地址即可。

## Linux / macOS

使用已有 Python 3.10 或更新版本，在仓库根目录执行：

```bash
python3 demo/viewer.py
```

无需第三方 Python 包。Windows 内置运行时仅用于 Windows。

## 演示范围

支持查看任务、技术方案、图层和素材，切换历史版本、播放和下载已有视频。用户选择的是浏览演示版，所以本包关闭上传、编辑、AI 修改和重新生成；所有查看操作都使用已保存的本地结果。

`manifest.json` 保存接口与素材映射及 SHA-256。`verify_package.py` 可在下载后检查文件完整性：

```powershell
.\demo\runtime\python.exe .\demo\verify_package.py
```

迁移检查结果见 `VALIDATION.json`。Python 运行时来源记录在 `runtime-source.json`，其许可文件位于 `runtime` 中。
