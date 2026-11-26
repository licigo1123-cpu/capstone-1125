# 漫剧制作台

面向单人创作的本地工作台。输入故事，制作约一分钟的 **9:16 竖屏漫剧**。创作者逐项审核分镜、设定、参考图、首帧与镜头视频，可以只重做有问题的镜头。模型、审核和导出由 Python 后端执行；浏览器负责编辑和预览。

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
| 图片 | H3 Image MLX，使用下方的图片运行时 | OpenAI Images 使用 generations / multipart edits；`muse-image-ext` 自动使用 Muse JSON generations、`aspect_ratio` 和 `image_urls`，保存任务号并轮询结果 |
| 视频 | H3，使用下方的视频运行时 | MiniMax 官方 H3 V2，或 APIMart Seedance；仅参考首帧，提交后保存任务号并轮询，下载实际 MP4 |

APIMart 视频基础地址填写 `https://api.apimart.ai/v1`。视频只参考一张已审核的首帧，可选 `seedance-1-0-pro-fast` / `seedance-1-0-pro-quality`（每镜 2–12 整秒），或 `seedance-2.0` / `seedance-2.0-fast` / `seedance-2.0-mini`（每镜 4–15 整秒）。程序不会自动更换付费模型或缩短镜头。模型与时长校验通过后，仅上传首帧至 APIMart 获取临时公共链接（显式选择保留 3 天），以 `image_with_roles` 的单个 `first_frame` 提交并保持 9:16。项目中仍保存原始图片和实际视频文件。

MiniMax 官方基础地址填写 `https://api.minimax.io`，模型 `MiniMax-H3`。视频地址末尾的 `/v1` 或 `/v2` 会按所用协议处理，避免重复拼成 `/v1/v2/video_generation`。APIMart 与 MiniMax 官方使用各自的账户和 API Key。

已有本地运行时可采用这个目录布局：

```text
父目录/
  capstone-1125/
  h3-runtime/        # scripts/generate.py、.venv、FL2VA 权重、Turbo LoRA
  h3-image-runtime/  # .venv/bin/h3-image-mlx、models/
  ollama-runtime/    # 可选，复用已有 Ollama models/
```

- **Qwen**：安装并启动 Ollama，拉取所选模型，例如 `ollama pull qwen3.5:4b`。已有相邻模型目录时，Mac 启动脚本会尝试复用。
- **自定义路径**：支持 `H3_RUNTIME`、`H3_IMAGE_RUNTIME`、`FFMPEG`、`STUDIO_DATA`。本地视频以 288×512 低内存配置生成，内存预算默认 20 GB，可用 `H3_MEMORY_GB` 调整。实际速度及能否生成 10–15 秒首帧参考镜头取决于可用统一内存和 Metal 设备。最终导出为 720×1280。

使用 Muse 时，三视图采用 3:2，场景图和首帧采用 9:16；最多 9 张参考图，原始文件总计不超过 11 MiB。异步任务号记录在制作任务中，完成后下载 WebP 并转换为项目中的 PNG 素材。接口说明见 [Muse Image API](https://apimart.ai/zh/model/muse-image)。

## 制作一集

| 步骤 | 操作与产物 |
| --- | --- |
| 1 · 剧本分镜 | 先在 **风格设定** 中保存画风，后续三视图、环境图、首帧和生成的视频都会使用它。粘贴故事，点击 **拆分为分镜**。文本模型按剧情转折和场景变化决定 1–15 镜，每镜 4–15 整秒。初稿含 4 镜及以上时合计 60 秒，简单故事可更短。蓝色场景含剧情、首尾状态和时长，黄色角色、绿色对白各自存储。三个区域都支持手动编辑、按建议修改、独立审核和版本查看。可下载分镜与对白 JSON。 |
| 2 · 角色设定 | 确认该角色出场镜头的场景和角色字段，在该角色卡点击 **自动生成初版**，根据其出场分镜生成该角色的外观描述，手动调整并审核后生成正、侧、背三视图。同名角色跨镜复用设定和三视图。 |
| 3 · 场景环境 | 点击 **自动生成初稿**，根据已确认的分镜场景提取地点、时段、布局和光线，编辑并审核后生成无人物环境图。在页面顶部的 **场景复用** 区域可按地点和时间合并场景，优先复用已有环境图；每个共享组显示一份环境设定和参考图。各分镜可选择自己的环境或共用同地点、同时段的环境。 |
| 4 · 首帧关键帧 | 每镜只制作一张首帧并审核。提交该镜的环境图及全部出镜角色的三视图，描述动作开始时的构图。图片制作不拼接对白。 |
| 5 · 分镜视频 | 可在目标分镜卡点击 **手动导入视频（MP4）**，上传最多 256 MiB 的本地视频，预览并审核。使用模型生成时，先确认首帧、剧情和对白，再将首帧、剧情及镜头时长交给视频模型。仅传一张首帧参考图，剧情中的结束状态作为文字动作目标。对白作为声音提示。播放检查，记录意见。若首帧正确而视频变脸，可只重做该镜视频。 |
| 6 · 成片导出 | 所有镜头视频审核通过后，按分镜顺序经 FFmpeg 合成 H.264/AAC MP4，720×1280、24 fps。保留模型生成的音轨，无音轨镜头补静音。台词的实际发音和对齐程度取决于视频模型。 |

使用页面标题下的 **本页目录 → 快速定位…** 可跳到对应分镜、角色或场景。分镜时长是制作计划，导入视频的实际长度会影响最终片长。

每张已有版本的分镜、设定和媒体卡都有 **历史版本** 入口，可查看内容、预览素材和审核意见，点击 **回退到版本 N** 将该内容追加为待审核的新版本。回退保留生成时的上游引用和风格；存在差异时仍显示「上游已变更」，历史记录会列出对应上游版本。还原并确认上游内容后，与该内容匹配的已有素材可以继续使用。改对白只影响对应视频和成片。已拆镜的项目保留原文，新故事从 **历史项目 → 新建项目** 开始。

旧项目的尾帧图片及历史版本继续保存在项目包中，不再提供尾帧生成入口。新视频不依赖尾帧；旧视频版本保留生成时的实际上游记录，可继续查看、审核、回退和导出。分镜中的「结束时刻」仍用于描述动作目标，不再生成尾帧图片。

共享关系以项目中的 `scene_sources` 保存，并随项目包和分镜 JSON 导出。合并时按地点名称及时间段匹配；地点或时段修改后，不再符合条件的分镜自动使用本镜环境。共享环境的编辑和重新生成会使相关分镜的关键帧提示重新制作。已有项目默认逐镜独立，可在场景页选择合并或指定来源。

### 本地拆镜格式

本地 Qwen 返回八列分镜表，程序解析为同一份结构化 JSON。镜号、列数、角色、时段及首尾画面会进行校验，根据模型输出的实际镜数分配整秒，余秒按镜头顺序补齐。表格结构有误时会携带校验反馈重试一次，最终失败会显示在工作台。云端文本 API 使用 JSON 对象输出。

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

`skills/` 中的三个项目技能分别对应分镜、角色和环境。运行时加载对应的 `SKILL.md` 指令，由工作流校验结构、时长及审核依赖。设计参考 [shuohao-skills](https://github.com/eternityspring/shuohao-skills) 中的 [novel-storyboard](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-storyboard)、[novel-characters](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-characters) 和 [novel-art](https://github.com/eternityspring/shuohao-skills/tree/main/skills/novel-art)。分镜保留开始与结束的文字状态，画面制作仅生成首帧。

接口依据：[MiniMax H3 创建任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)、[查询任务](https://platform.minimax.io/docs/api-reference/video-generation-v2-query)、[APIMart Seedance 1.0](https://docs.apimart.ai/en/api-reference/videos/doubao/generation.md)、[Seedance 2.0](https://docs.apimart.ai/en/api-reference/videos/seedance-2-0/generation.md)、[参考图上传](https://docs.apimart.ai/en/api-reference/uploads/images.md)、[任务查询](https://docs.apimart.ai/en/api-reference/tasks/status.md)、[OpenAI 图片生成与编辑](https://developers.openai.com/api/docs/guides/image-generation)。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用临时数据目录。文本和生图、生视频由测试替身提供，拼接使用真实 FFmpeg。覆盖分镜纠错、审核依赖、失效传播、单首帧传参、旧版尾帧历史兼容、项目包往返、密钥隔离、版本冲突、媒体范围读取和 1 分钟竖屏 MP4 的画幅与音轨。模型画质与角色一致性须用实际模型制作样片后人工审核。

界面参考 [Awesome Design](https://github.com/gztchan/awesome-design) 收录的 Material Design Palette。
