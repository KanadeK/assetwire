# assetwire 中文说明

验证静态部署是否真正返回构建时的文件字节及正确 MIME。HTTP 200 不代表 JavaScript 文件可用：服务器可能返回 HTML 回退页、旧缓存文件或损坏的 gzip。

需要 Python 3.11+，没有运行时第三方依赖。按英文 README 克隆、安装后运行 `python examples/demo.py`，会启动本地临时 HTTP 服务，证明正常资源通过、注入的 200 HTML 回退失败，然后清理临时文件。

实际使用：

```sh
assetwire manifest dist --file assets/app.js --file assets/app.css -o release-assets.json
assetwire check release-assets.json --base https://static.example.test/releases/v1/ --json
```

把路径和 URL 换成自己的公开构建资源及部署地址；base 必须以 `/` 结尾。只选明确准备公开的文件，工具不会递归收集其他文件。清单只包含相对路径、大小、哈希和 MIME，不上传本地内容。默认检查 identity 和 gzip 请求，拒绝跟随重定向，不保存或输出响应体。

退出码：0 全部通过，1 部署或网络检查失败，2 输入错误。当前只支持 identity/gzip、标准 TLS 和无登录的 HTTP(S)，不检查 JS 运行行为、浏览器旧缓存或 Brotli；完整边界和资源大小限制见 [英文 README](README.md)。卸载：`python -m pip uninstall assetwire`，自行删除生成的清单即可。
