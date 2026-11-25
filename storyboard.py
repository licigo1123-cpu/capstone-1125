"""Convert the local model's flat shot table to the canonical project schema."""
import re


INSTRUCTION = """你是竖屏漫剧导演。用户消息就是待改编的故事原文。完整保留起因、转折和结局，按剧情进展或场景变化分成4至6镜，角色名称始终一致。
只输出下面八列的中文表格，每镜一行，列间使用 |，单元格内不要换行或使用 |。不要输出 JSON 或解释。
镜号 | 地点 | 时段 | 出镜角色 | 动作与运镜 | 开始画面 | 结束画面 | 对白
镜号从1连续递增。时段只选白天、黄昏、夜晚。动物也是角色，角色名用顿号分隔，空镜写无。
动作与运镜写连续可见动作和景别，开始画面和结束画面分别写动作起止时的姿态与空间位置，不写台词。
对白写“角色：原话”，多句用“；角色：原话”连接，无对白写无。每镜对应10至15秒，总时长由程序分配为60秒。
"""
EMPTY = {"", "无", "无对白", "无角色", "空镜", "—", "-"}


def parse(reply):
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("Qwen 没有返回分镜正文")
    shots = []
    for lineno, line in enumerate(reply.splitlines(), 1):
        line = line.strip().replace("｜", "|")
        if not line or line.startswith("```"):
            continue
        if "|" not in line:
            if re.match(r"^(?:第|分镜|镜头)?\s*\d", line):
                raise ValueError(f"分镜表第 {lineno} 行缺少列分隔符")
            continue
        row = [v.strip().strip("*").strip() for v in line.strip("|").split("|")]
        if ("地点" in row and "对白" in row) or all(re.fullmatch(r"[\s:\-]+", x) for x in row):
            continue
        if len(row) != 8:
            raise ValueError(f"分镜表第 {lineno} 行有 {len(row)} 列，需要 8 列")
        number, place, period, cast, plot, first, last, dialogue = row
        idx = re.fullmatch(r"(?:第|分镜|镜头|S)?\s*0*(\d+)\s*(?:镜|号|[.、])?", number, re.I)
        if not idx or int(idx[1]) != len(shots) + 1:
            raise ValueError(f"分镜表第 {lineno} 行镜号应为 {len(shots) + 1}")
        lines = []
        if dialogue not in EMPTY:
            for part in re.split(r"[；;]\s*(?=[^：:；;]{1,40}[:：])", dialogue):
                match = re.fullmatch(r"\s*([^：:]{1,40})[:：]\s*(.+)", part)
                if not match:
                    raise ValueError(f"镜头 {number} 的对白缺少说话人或台词")
                speaker, text = (v.strip() for v in match.groups())
                if len(text) > 1 and (text[0], text[-1]) in [("“", "”"), ('"', '"'), ("「", "」")]:
                    text = text[1:-1].strip()
                lines.append(dict(speaker=speaker, text=text))
                plot, first, last = (v.replace(text, "").strip() for v in (plot, first, last))
        shots.append(dict(scene=dict(place=place, time=period, plot=plot, first=first, last=last),
                          cast=[] if cast in EMPTY else [v.strip() for v in re.split(r"[、,，；;]", cast) if v.strip()],
                          dialogue=lines))
    if not 4 <= len(shots) <= 6:
        raise ValueError(f"分镜表需要 4–6 镜，收到 {len(shots)} 镜")
    for shot in shots:
        shot["scene"]["seconds"] = 60 // len(shots)
    return {"shots": shots}
