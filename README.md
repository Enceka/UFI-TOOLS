# UFI-TOOLS for Linux

> 把 UFI-TOOLS 装在一台 Linux 手持终端上（本仓库面向 E5-LINUX 这类 Debian + systemd 架构），
> 同一局域网内的手机、平板、电脑打开 `http://<设备IP>:2333/`，就能用 UFI-TOOLS 的功能
> **控制这台设备本身**：状态监控、蜂窝数据、Wi-Fi 热点、接入设备、AT 指令、脚本与定时任务、
> 插件与主题、短信/状态转发。

后端用 Python 标准库重写（无第三方依赖），Web 前端与签名鉴权沿用不变；面向中兴随身 WiFi 的
`goform` 厂商协议层已移除，控制改由 systemd / hostapd / sysfs 直接驱动。

- 详细文档：[`linux/README.md`](linux/README.md)（安装、能力对照、配置速查、接口一览、测试）
- 上游 Android 版：[`kanoqwq/UFI-TOOLS`](https://github.com/kanoqwq/UFI-TOOLS)（本仓库不再包含 Android 应用源码）

## 快速开始

```sh
cd linux
sudo ./install.sh                 # 安装到 /usr/lib/ufi-tools + systemd 单元，默认口令 admin
# 或先在源码目录试跑：
./bin/ufi-tools --data-dir /tmp/ufi-tools serve --port 2333
```

然后浏览器访问 `http://<设备IP>:2333/`，用生成的口令登录。命令行工具：

```sh
ufi-tools status                                  # 各子系统状态（AT / 热点 / 蜂窝 / 性能 / 指示灯）
ufi-tools clients                                 # 当前接入的无线客户端
ufi-tools set-token 'NewPass123'                  # 改口令，运行中 2 秒内生效
ufi_req -e /api/linux/overview                    # 自动用本机已存口令签名
ufi_req -X POST -e /api/linux/power -d '{"action":"reboot"}'
```

## 仓库结构

| 路径 | 内容 |
|---|---|
| `linux/ufitools/` | 服务端实现：鉴权、HTTP 路由、设备信息、AT 通道、原生控制、modem 缓存、转发、任务 |
| `linux/www/` | Web 前端（纯静态，无构建步骤） |
| `linux/www-linux/` | 前端 shim：适配上游界面（登录只缺口令、隐藏做不到的入口、增加「E5 控制台」） |
| `linux/tests/` | 148 个 `unittest` 用例（单元 + 端到端） |
| `linux/systemd/`、`linux/install.sh`、`linux/bin/` | 部署物 |
| `API_Doc.md` | 上游 HTTP API 参考（Linux 端口仍实现其中与前端相关的部分） |
| `LICENCE` | MIT |

## 许可

MIT，见 [`LICENCE`](LICENCE)。UFI-TOOLS 项目由 [kanoqwq](https://github.com/kanoqwq/UFI-TOOLS) 发起；
本移植保留原有署名，并沿用同样的开源许可。
