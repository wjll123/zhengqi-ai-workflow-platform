# macOS 离线安装

1. 在目标 Mac 安装 Python 3.9 或更高版本。
2. 解压本目录，运行 `./install_offline.sh`。安装过程不会访问网络。
3. 运行 `./run_offline.sh`，浏览器打开 `http://127.0.0.1:8848`。
4. 停止服务可按 `Ctrl+C`，或运行 `./stop.sh`。

本包针对 macOS Apple Silicon 构建，包含 Python wheel 缓存和 LibreOffice。企查查查询支持本机模式和桌面助手模式：云端平台派发工单时，在执行电脑运行 `桌面查询助手.command`，它会主动领取工单并使用本机已登录浏览器查询。桌面助手配置见 `docs/企查查桌面助手部署.md`。

Chrome/Edge 需要调试模式，Safari 可直接使用。验证码和风控验证仍由使用者在本机浏览器中处理。
