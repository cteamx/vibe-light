# Vibe Light

[English](README.md)

<video src="./images/x.mp4" controls=""></video>

**Vibe Light** 通过 hook 事件驱动 **Yeelight** 灯带，把 AI 编程助手的工作状态同步成桌面氛围灯效果。支持多任务状态聚合，多个任务同时运行时也能保持灯光状态稳定。

我这条 **Yeelight** 灯带是两年前买的，目前已经停产。现在有新款在售，购买前建议先咨询客服，确认设备是否支持开启局域网控制。

Yeelight 灯带可以在淘宝、拼多多、京东、亚马逊等平台购买；海外用户也可以看看 Amazon、AliExpress、Temu 等平台。

## 效果状态

| 助手状态 | 触发事件 | 灯带效果 |
| --- | --- | --- |
| 正在思考/运行 | `thinking`、`running` | 蓝紫色呼吸流光 |
| 等待授权 | `need_approval` | 洋红色常亮 |
| 已完成/空闲 | `done` | 白色常亮 |
| 手动重置 | `reset`、`clear` | 清空状态后恢复空闲 |

## 文件说明

- `hooks.json`：hooks 配置示例，把不同 hook 事件转成脚本命令。
- `vibe-light/yeelight_status.py`：状态聚合和灯带控制脚本。

## 使用前准备

1. 安装 Python 3。

2. 安装 Yeelight Python 包：

```bash
pip3 install yeelight
```

3. 在 **Yeelight App** 中开启设备的局域网控制。可以下载 **Yeelight Classic App**，开启局域网控制后，再到路由器后台查看设备 IP。

![](images/1.png)

## 修改 IP

修改 `vibe-light/yeelight_status.py` 里的 `BULB_IP`，填成自己的灯带 IP。

```python
# Yeelight device address. Change this to your light strip or bulb IP.
BULB_IP = "192.168.3.57"
```

## 接入 hooks

把 `hooks.json` 里的 `hooks` 配置合并到支持 hooks 的编程助手配置中，并确保命令里的脚本路径可用：

```json
{
  "type": "command",
  "command": "python3 ~/.codex/vibe-light/yeelight_status.py thinking"
}
```

如果脚本放在其他位置，需要把命令里的路径改成实际路径。

## 开始使用

把 `hooks.json` 放到 `~/.codex/` 目录下。

把 `vibe-light` 放到 `~/.codex/` 目录下。

在 Codex 中输入 `/hook`，按提示开启 hook 权限即可。

![](images/2.png)

## 手动调试

手动触发运行效果：

```bash
python3 vibe-light/yeelight_status.py running
```

手动触发等待授权效果：

```bash
python3 vibe-light/yeelight_status.py need_approval
```

手动切换到完成/空闲状态：

```bash
python3 vibe-light/yeelight_status.py done
```

手动清空状态：

```bash
python3 vibe-light/yeelight_status.py clear
```

## 可配置项

- `BULB_IP`：Yeelight 灯带或灯泡的局域网 IP。
- `STATE_PATH`：状态文件路径，默认 `/tmp/vibe-light-status.json`。
- `LOCK_PATH`：锁文件路径，默认 `/tmp/vibe-light-status.lock`。

## 灯光颜色

灯光效果在 `apply_light()` 函数中修改。想换颜色时，优先调整这些 RGB 和亮度参数即可，最后一个参数是灯光亮度。

### 静态灯

```python
def apply_light(status):
    """Map the aggregated assistant status to the physical light effect."""
    bulb = get_bulb()
    if status == "running":
        start_thinking(bulb)
    elif status == "need_approval":
        set_solid(bulb, 255, 0, 217, 100)
    elif status == "done":
        set_solid(bulb, 255, 255, 255, 100)
```

### 动态呼吸灯

`running` 调用 `start_thinking()`，修改这里可以调整呼吸流光。

动态呼吸灯也可以改成多种颜色切换，比如蓝 -> 红 -> 绿 -> 黄 -> 紫 -> 白。不过长时间使用容易分散注意力，建议保持单一颜色呼吸，观感更舒服。

```python
def start_thinking(bulb):
    """Start the blue-purple breathing effect used while the assistant works."""
    stop_effect(bulb)
    bulb.turn_on()
    flow = Flow(
        count=0,
        action=Flow.actions.recover,
        transitions=[
            RGBTransition(40, 0, 255, duration=900, brightness=25),
            SleepTransition(duration=120),
            RGBTransition(40, 0, 255, duration=900, brightness=100),
            SleepTransition(duration=120),
        ],
    )
    bulb.start_flow(flow)
```
