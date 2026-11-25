# 纸幕 · 漫剧制作台

面向单人创作的本地工作台。输入故事，制作约一分钟的 **9:16 竖屏漫剧**。创作者逐项审核分镜、设定、参考图、首尾帧与镜头视频，可以只重做有问题的镜头。模型、审核和导出由 Python 后端执行；浏览器负责编辑和预览。

## 在 Mac 上启动

需要 Python **3.11+**。视频合成和云端图片画幅转换需要带 libx264、AAC 的 FFmpeg。可使用系统 FFmpeg，或复用相邻 H3 环境中的 FFmpeg。

```sh
git clone https://github.com/licigo1123-cpu/capstone-1125.git
cd capstone-1125
zsh start.command
```

也可以直接运行 `python3 app.py`，打开 **http://127.0.0.1:8767**。Python 服务只依赖标准库。可用 `--port` 更换端口。每份数据目录运行一个服务实例。

### 连接设置

三个模型可分别选择本地或 API，例如本地 Qwen 拆镜、本地 H3 出图、MiniMax 云端制作视频。右上角 **连接设置** 可填写基础地址、模型名和 API Key。远端生成使用所填账户的额度。

| 功能 | 本地 | 远端协议 |
| --- | --- | --- |
| 文本 | Ollama，默认 `qwen3.5:4b`，基础地址 `http://127.0.0.1:11434` | OpenAI 兼容 Chat Completions，支持 JSON 对象输出；基础地址包含 `/v1` |
| 图片 | Apple Silicon 上的 `h3-image-mlx`，支持多张参考图 | OpenAI Images 协议。无参考图调用 `/images/generations`，有参考图以 multipart 将环境及角色图逐张传给 `/images/edits` |
| 视频 | MiniMax-H3 MLX，两个 `--image` 分别对应 `first`、`last` | MiniMax H3 V2。基础地址默认 `https://api.minimax.io`，提交后轮询任务，取回实际 MP4 |

已有本地运行时可采用这个目录布局：

```text
父目录/
  capstone-1125/
  h3-runtime/        # scripts/generate.py、.venv、FL2VA 权重、Turbo LoRA
  h3-image-runtime/  # .venv/bin/h3-image-mlx、models/
  ollama-runtime/    # 可选，复用已有 Ollama models/
```

- **Qwen**：安装并启动 Ollama，拉取所选模型，例如 `ollama pull qwen3.5:4b`。已有相邻模型目录时，Mac 启动脚本会尝试复用。
- **本地 H3 视频**：按 [minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac) 安装运行时，适配版本为 `3fcc9f8c557cf353400b1acf737b985e524a509f`。准备好 FL2VA 和 BF16 Turbo LoRA 后，执行 `zsh scripts/apply_h3_i2v_patch.zsh`。它会检查低内存图生视频和 PIL 参考图处理补丁。参考图处理走 MLX/PIL，避免加载无关的 PyTorch 视频处理器。
- **本地 H3 图片**：已有运行时可直接复用。首次安装可执行 `zsh scripts/setup_h3_image.zsh`，按提示确认模型许可后下载权重。
- **自定义路径**：支持 `H3_RUNTIME`、`H3_IMAGE_RUNTIME`、`FFMPEG`、`STUDIO_DATA`。本地视频以 288×512 低内存配置生成，内存预算默认 20 GB，可用 `H3_MEMORY_GB` 调整。实际速度及能否生成 10–15 秒双帧镜头取决于可用统一内存和 Metal 设备。最终导出为 720×1280。

## 制作一集

| 步骤 | 操作与产物 |
| --- | --- |
| 1 · 剧本分镜 | 输入故事并拆镜。初稿必须是 4–6 镜，每镜 10–15 整秒，合计 60 秒。蓝色场景含剧情、首尾状态和时长，黄色角色、绿色对白各自存储。三个区域都支持手动编辑、按建议修改、独立审核和版本查看。可下载分镜与对白 JSON。 |
| 2 · 角色设定 | 确认该角色出场镜头的场景和角色字段，在角色页生成设定卡，手动调整并审核后生成正、侧、背三视图。同名角色跨镜复用设定和三视图。 |
| 3 · 场景环境 | 根据该镜已确认场景分析地点、时段、布局、光线与道具。也可手动填写。确认描述后生成一张无人环境参考图，审核通过后供该镜使用。 |
| 4 · 首尾关键帧 | 首帧和尾帧分别制作，分别审核。每次提交该镜的环境图及全部出镜角色的三视图，并分别描述动作开始或结束时的构图。图片制作不拼接对白。 |
| 5 · 分镜视频 | 确认两帧和对白后，将首帧、尾帧、剧情和时长交给 H3。对白作为声音提示。播放检查，记录意见。若关键帧正确而视频变脸，可只重做该镜视频。 |
| 6 · 成片导出 | 所有镜头视频审核通过后，按分镜顺序经 FFmpeg 合成 H.264/AAC MP4，720×1280、24 fps。保留模型生成的音轨，无音轨镜头补静音。台词的实际发音和对齐程度取决于视频模型。 |

上游版本变化会使关联的下游产物显示「上游已变更」。改对白只影响对应视频和成片。历史版本可查看图像、播放视频、查看意见；上游版本仍匹配时可恢复为待审核的新版本。已拆镜的项目保留原文，新故事从 **历史项目 → 新建项目** 开始。

### 本地拆镜格式

本地 Qwen 返回八列分镜表，程序解析为同一份结构化 JSON。镜号、列数、角色、时段及首尾画面会进行校验，4、5、6 镜分别分配每镜 15、12、10 秒。表格结构有误时会携带校验反馈重试一次，最终失败会显示在工作台。云端文本 API 使用 JSON 对象输出。

## 存储与迁移

| 数据 | 存储位置和形式 |
| --- | --- |
| 项目、版本、审核、任务 | `data/studio.sqlite3`。SQLite 事务保存 UTF-8 JSON 项目文档和独立任务表，记录任务状态、失败原因和云端视频任务号。 |
| 分镜、对白 | 分镜有稳定编号，例如 `S01.scene`、`S01.cast`、`S01.dialogue`。对白单独保存为 `[{ "speaker": "小禾", "text": "我在这里。" }]`。画面生成从场景和角色取值。 |
| 版本 | 每份文字和媒体的版本均附来源、时间、建议、审核事件及上游版本映射。媒体版本只保存文件标识。 |
| 图片、视频、成片 | `data/media/<SHA-256>.<ext>`，保存实际二进制文件。内容相同的素材共享一个文件；下载地址过期不会影响已保存的项目。 |
| API Key | `data/connections.json` 位于本机，权限为 `0600`，以明文存储。浏览器仅取得是否已设置的标记。项目包和 Git 均排除本机连接配置。 |
| 迁移 | **保存项目** 下载 ZIP，包含带 `schema_version` 的 `project.json` 和当前、历史版本引用的所有媒体。**导入项目** 核验版本、依赖引用、文件清单和 SHA-256，然后另存为新项目。支持本工作台导出的 ZIP。 |

任务在单个后台 worker 中串行运行，刷新页面后可继续查看进度。服务重启会把未完成的任务标记为「已中断」。已提交云端的任务可能继续计费，任务记录可查看已取得的远端任务号。

## 技能与接口依据

`skills/` 中的三个项目技能分别对应分镜、角色和环境。运行时加载对应的 `SKILL.md` 指令，由工作流校验结构、时长及审核依赖。设计参考 [shuohao-skills](https://github.com/eternityspring/shuohao-skills) 中的 [novel-storyboard](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-storyboard)、[novel-characters](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-characters) 和 [novel-art](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-art)，按本项目的首尾帧流程编写。

接口依据：[MiniMax H3 创建任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)、[查询任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)、[OpenAI 图片生成与编辑](https://developers.openai.com/api/docs/guides/image-generation)。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用临时数据目录。文本和生图、生视频由测试替身提供，拼接使用真实 FFmpeg。覆盖分镜纠错、审核依赖、失效传播、首尾帧传参、项目包往返、密钥隔离、版本冲突、媒体范围读取和 1 分钟竖屏 MP4 的画幅与音轨。模型画质与角色一致性须用实际模型制作样片后人工审核。
