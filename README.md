# 游戏搭子 · SceneMate

一个 Windows 桌面 AI 游戏搭子：定时截取游戏画面，判断这一刻值不值得开口，再让带视觉能力的模型用角色口吻说一两句，以字幕浮层显示在游戏上。

它不是一个“每 5 秒念一句画面”的解说器。中间有信号层、调度层和契约层：画面没变化、没值得说的事、或者她上一句还没接住，这一格就会留白。

## 普通用户：下载即用（推荐）

不想装 Python，直接下载便携版：

**https://github.com/qingshouya777/SceneMate/releases/latest**

1. 下载 `画面搭子_便携包.zip`（Windows 10/11 x64）；
2. **完整解压**到一个普通文件夹（不要直接在压缩包里双击）；
3. 双击 `启动.bat`。

便携包内置 Python 3.14 和运行依赖，不需要另外装环境。  
第一次打开：**设置 → 连接**填自己的 API Key；**设置 → 常用**选择游戏窗口或框选画面区域。

## 功能

- **看画面说话**：截图 → 视觉信号（文字块 / 气泡 / 三环 / 流）→ 调度 → 视觉模型 → 字幕。
- **线程化对话**：读字、气泡、过渡、审美、回访、故事；一条话题有自己的开头、中间和收尾。
- **故事与“看一个地方”**：故事随机 4~6 格；看一个地方固定走完请求 → 看一会 → 离开。
- **长期记忆**：最近原话 + 旧聊天压缩后的长期摘要一起进提示词；可在设置里查看、编辑、清空。
- **可换接入**：DeepSeek / OpenAI / 通义千问 / Kimi / 智谱 / 硅基流动 / OpenRouter / 任意 OpenAI 兼容接口。
- **桌面浮层**：字幕置顶、可拖动；控制条是普通窗口，可最小化到任务栏。
- **本机隐私**：API Key 用 Windows DPAPI 加密保存；配置、记忆和 Key 默认不写进程序目录。

## 环境

- Windows 10/11 x64
- Python 3.14 x64（源码运行）
- 依赖：`mss`、`Pillow`

如果不想装 Python，直接下载上面的便携版即可。

## 快速开始（源码）

```bat
git clone https://github.com/qingshouya777/SceneMate.git
cd SceneMate
app\准备环境.bat
启动.bat
```

`app\准备环境.bat` 会把依赖装到 `app\runtime\`，不污染系统 Python。  
`启动.bat` 会优先使用本机的 `C:\Python314\pythonw.exe`，没有则使用 `PATH` 里的 Python。

第一次打开后：

1. **设置 → 连接**：选服务商，填自己的 API Key，点「保存到本机」。
2. **设置 → 常用**：选择游戏窗口，或框选要截的画面区域。
3. 控制条上点「开始」。

> 这个项目需要**视觉模型**。DeepSeek 官方接口的 `deepseek-flash` / `deepseek-chat` 已实测可看图；`deepseek-v4-pro` 不看图。换别家时请选模型名里带 `vl` / `vision` / `4o` 之类、或先点「测试连接」确认“② 看图：看得见”。

## 数据放在哪

默认：

```text
%LOCALAPPDATA%\游戏搭子\
  config.json      配置
  memory.json      聊天/长期记忆
  state.json       好感等状态
  keys.dat         API Key（DPAPI 加密，只绑当前 Windows 账户）
  日志.txt / 调度.txt
```

如果这个目录写不进去（例如程序放在受限目录），会自动退回：

```text
app\data\
```

这些文件都不进 git。换机器或换 Windows 用户需要重新填 Key。

## 目录

```text
启动.bat                 启动入口
app/
  main.py                截图 → 调度 → 模型 → 字幕 的主循环
  brain.py               提示词拼装、模型调用、解析与重说
  contract.py            契约、A1/A3/A4 和各类硬/软判据
  router.py              信号事件、线程、抢占、pending、故事节奏
  memory.py              token 预算、历史、长期摘要、压缩
  settings_ui.py         设置面板
  overlay.py             字幕浮层 / 控制条
  branding.py            窗口标题与黑白手柄图标
  prompts/               正在使用的提示词
  prompt_defaults/       提示词默认值（缺失时回退）
  assets/                图标
  tools/                 标定工具
  docs/                  说明和示例图
  config.json            本机配置（不进 git）
  runtime/               本机依赖（不进 git）
```

## 说明

- 这个仓库只放源码、提示词和脚本；便携包/运行库不提交，放在 Releases。
- 记忆是“长期摘要 + 最近原话”，不是逐字保存全部聊天。
- 模型调用会产生费用；请不要把 Key 提交到仓库。
- 画面、角色名和聊天内容都可能属于个人数据，提交 issue/截图前请先检查。

## License

MIT，见 [LICENSE](LICENSE)。
