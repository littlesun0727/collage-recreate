# 图像素材生成与恢复

本工具只调用已选定的 qwen-image-3.0-pro，通过 DashScope 同步接口：
`POST /api/v1/services/aigc/multimodal-generation/generation`。
模型固定在代码中；没有模型回退。服务是否返回同名模型独立记录为 reported_model，没有返回时为 null。

## 配置

以 config.example.json 为基础，在源码库之外创建私有配置：

```json
{
  "base_url": "https://dashscope.aliyuncs.com",
  "api_key_env": "DASHSCOPE_API_KEY",
  "timeout_seconds": 180,
  "download_hosts": ["实际服务返回的下载域名"],
  "prompt_extend": false,
  "watermark": false
}
```

可用 api_key_file 指向已有本地凭据文件；如为 JSON，再用 api_key_json_pointer（如 /api_keys/0）取字符串。相对凭据路径以配置文件所在目录解析。不要把密钥写入配置、提示词、日志或项目文件。

本地审计代理可使用 base_url=http://127.0.0.1:PORT 并显式 allow_loopback_http=true。HTTP 仅允许数值回环地址；不跟随重定向、不开系统代理。下载只允许 HTTPS 和列明的精确域名，不携带 API 密钥。

## 请求

init 时按已有用户授权设置 permissions.image_generation、max_requests、max_requests_per_asset 和 upload_asset_ids。默认关闭、预算 0；不继承其他任务的权限。

```json
{
  "asset_id": "paper_texture",
  "prompt": "根据参考风格制作一个独立纸纹素材，保持边缘清晰，画面不含文字。",
  "references": [{"asset":"reference","role":"reference_style"}],
  "size": "1024x1024"
}
```

最多三张参考；只上传名单内的图像资源，并先归一化、去除 EXIF。精确客户照片通常保留为独立照片层，别为了装饰生成上传无关客户内容。每次请求固定 n=1。返回图片自动绑定 asset_id，布局仍由 apply 明确指定。

调用：

```powershell
python scripts/run.py generate --task TASK --input generation.json --config PRIVATE_CONFIG
```

## 状态与恢复

.state/requests.json 记录请求前落盘的提交状态、请求指纹、调用次数、HTTP/请求标识和耗时；完整响应和可能带签名的下载地址只存在私有 .state/remote，不导出。

- completed：校验本地 SHA-256 后复用。文件损坏或缺失时使用 resume 从保存响应恢复，不重新生图。
- response_saved：接口已经返回；下载域名、网络、图片解码或模型不符问题可在原请求上检查。修复下载设置后执行 resume。
- submitted/unknown：进程中断、超时、5xx、不可读成功响应都可能已经收费；整个任务阻止新提交，不能改参数绕开。
- failed：明确 4xx。只有经检查后显式使用 generate --retry-failed 才重试，并继续占用原任务和素材的预算；没有自动重试。

```powershell
python scripts/run.py resume --task TASK --request REQUEST_KEY --config PRIVATE_CONFIG
```

未知请求需先从服务/操作记录核对；若找回原响应，可传 `--response recovered.json --evidence evidence.txt`。工具记录人工恢复来源和证据摘要，已有请求 ID 时要求匹配；这不是工具独立核实服务结果的证明。无法核对时保留 unknown，不用另建任务绕过。

白底或 RGB 输出依然可能是完全不透明。检查输出 alpha_range 和实际图像后再决定怎么用；工具不提供自动抠图，也不自动声明风格或视觉通过。
