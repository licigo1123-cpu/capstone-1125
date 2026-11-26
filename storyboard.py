"""Convert the local model's flat shot table to the canonical project schema."""
import re


INSTRUCTION = """你是竖屏漫剧导演。用户消息就是待改编的故事原文。完整保留起因、转折和结局，先分析场景切换、关键动作和情绪转折，再自行决定镜头数。一个连续动作可以只用一镜，复杂段落可以多镜展开，不要为了凑数重复画面。角色名称始终一致。
只输出下面八列的中文表格，每镜一行，列间使用 |，单元格内不要换行或使用 |。不要输出 JSON 或解释。
镜号 | 地点 | 时段 | 出镜角色 | 动作与运镜 | 开始画面 | 结束画面 | 对白
镜号从1连续递增。时段只选白天、黄昏、夜晚。动物也是角色，角色名用顿号分隔，空镜写无。
动作与运镜写连续可见动作和景别，开始画面和结束画面分别写动作起止时的姿态与空间位置，不写台词。
对白写“角色：原话”，多句用“；角色：原话”连接，无对白写无。目标片长约一分钟，每镜4至15秒，最多15镜。简单故事可少于4镜。
"""
EMPTY = {"", "无", "无对白", "无角色", "空镜", "—", "-"}
QUOTES = [("“", "”"), ('"', '"'), ("「", "」"), ("‘", "’"), ("'", "'")]


def unquote(text):
    text = text.strip()
    while len(text) > 1 and (text[0], text[-1]) in QUOTES:
        text = text[1:-1].strip()
    return text


def no_dialogue(text):
    text = unquote(text).lower()
    if len(text) > 1 and (text[0], text[-1]) in [("（", "）"), ("(", ")")]:
        text = text[1:-1].strip()
    return text in EMPTY or text in {"无台词", "无对话", "无对白。", "none", "null", "n/a", "--"} or bool(
        re.fullmatch(r"(?:无|无对白|无台词|无对话)[。.!！]?(?:[（(].*[）)])?", text))


def dialogue_lines(dialogue):
    dialogue = unquote(dialogue)
    if no_dialogue(dialogue):
        return []
    lines = []
    # Models sometimes use HTML breaks inside a Markdown table cell.
    for part in re.split(r"<br\s*/?>|[；;]\s*(?=[^：:；;]{1,40}[:：])", dialogue, flags=re.I):
        part = unquote(part)
        if no_dialogue(part):
            continue
        match = re.fullmatch(r"\s*([^：:]{1,40})[:：]\s*(.+)", part)
        if match:
            speaker, text = (unquote(v) for v in match.groups())
        else:
            speaker, text = "待确认", part
        if not speaker or not text:
            speaker, text = "待确认", part
        lines.append(dict(speaker=speaker, text=text))
    return lines


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
        lines = dialogue_lines(dialogue)
        for line in lines:
            plot, first, last = (v.replace(line["text"], "").strip() for v in (plot, first, last))
        shots.append(dict(scene=dict(place=place, time=period, plot=plot, first=first, last=last),
                          cast=[] if cast in EMPTY else [v.strip() for v in re.split(r"[、,，；;]", cast) if v.strip()],
                          dialogue=lines))
    if not 1 <= len(shots) <= 15:
        raise ValueError(f"分镜表需要 1–15 镜，收到 {len(shots)} 镜")
    seconds, remainder = divmod(min(60, 15 * len(shots)), len(shots))
    for index, shot in enumerate(shots):
        shot["scene"]["seconds"] = seconds + int(index < remainder)
    return {"shots": shots}
