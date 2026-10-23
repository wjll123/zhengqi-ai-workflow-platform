# macOS 离线安装

1. 在目标 Mac 安装 Python 3.9 或更高版本。
2. 解压本目录，运行 `./install_offline.sh`。安装过程不会访问网络。
3. 运行 `./run_offline.sh`，浏览器打开 `http://127.0.0.1:8848`。
4. 停止服务可按 `Ctrl+C`，或运行 `./stop.sh`。

本包针对 macOS Apple Silicon 构建，包含 Python wheel 缓存和 LibreOffice。企查查查询须在运行平台的 Mac 上使用已登录浏览器；优先支持 Chrome/Edge 调试模式，也可选择 Safari。请在平台的企查查页面查看调试启动命令。
