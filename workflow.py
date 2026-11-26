"""Typed project document. Every reviewed output records its exact upstream versions."""
import copy
import hashlib
import json
import re
import character_profiles
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SKILLS = Path(__file__).parent / "skills"
MEDIA = re.compile(r"[a-f0-9]{64}\.(png|jpg|webp|mp4)")
FIELDS = ("scene", "cast", "dialogue")
TIMES = ("白天", "黄昏", "夜晚")
DEFAULT_STYLE = "漫画插画"
STYLED_MEDIA = ("sheet", "plate", "first", "last", "video")


def skill(name):
    document = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    return document.split("\n---\n", 1)[1].strip()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new(title="未命名漫剧"):
    return dict(schema_version=1, id=uuid4().hex, title=title, story="", style=DEFAULT_STYLE, ratio="9:16",
                shots=[], nodes={}, scene_sources={}, updated=now(), revision=0)


def words(s):
    if not isinstance(s, str) or not s.strip():
        raise ValueError("文字字段不能为空")
    return s.strip()


def clean(kind, v):
    if kind == "scene":
        row = {k: words(v[k]) for k in ("place", "time", "plot", "first", "last")}
        if row["time"] not in TIMES or type(v["seconds"]) is not int or not 4 <= v["seconds"] <= 15:
            raise ValueError("时段应为白天、黄昏或夜晚，每镜时长为 4–15 整秒")
        return dict(row, seconds=v["seconds"])
    if kind in ("cast", "dialogue"):
        if not isinstance(v, list):
            raise ValueError("角色和对白需要使用数组")
        if kind == "cast":
            return list(dict.fromkeys(words(name) for name in v))
        return [{f: words(line[f]) for f in ("speaker", "text")} for line in v]
    if kind in ("profile", "environment"):
        return words(v)
    if not isinstance(v, str) or not MEDIA.fullmatch(v):
        raise ValueError("素材标识无效")
    if (kind in ("video", "film")) != v.endswith(".mp4"):
        raise ValueError("素材类型与步骤不符")
    return v


def character(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24]


def cast_names(p):
    return list(dict.fromkeys(n for s in p["shots"] for n in value(p, s + ".cast")))


def value(p, k):
    return p["nodes"][k]["versions"][-1]["value"]


def scene_identity(p, shot):
    scene = value(p, shot + ".scene")
    return " ".join(scene["place"].split()).casefold(), scene["time"]


def scene_owner(p, shot):
    links = p.get("scene_sources", {})
    visited = set()
    owner = shot
    while True:
        if owner not in p["shots"] or owner in visited:
            raise ValueError("共享场景引用无效或形成循环")
        visited.add(owner)
        source = links.get(owner, owner)
        if source == owner:
            break
        owner = source
    if any(scene_identity(p, s) != scene_identity(p, shot) for s in visited):
        return shot
    return owner


def environment_key(p, key):
    if "." in key:
        shot, kind = key.split(".")
        if kind in ("environment", "plate"):
            return scene_owner(p, shot) + "." + kind
    return key


def label(p, key):
    if key == "plan":
        return "剧本分镜"
    if key == "final.film":
        return "最终成片"
    owner, _, kind = key.partition(".")
    fields = dict(scene="场景与剧情", cast="角色信息", dialogue="对白", profile="设定卡",
                  sheet="三视图", environment="环境设定", plate="环境参考图", first="首帧", last="尾帧", video="视频")
    if owner in p["shots"]:
        if kind in ("environment", "plate"):
            owners = list(dict.fromkeys(scene_owner(p, s) for s in p["shots"]))
            prefix = "场景" + str(owners.index(scene_owner(p, owner)) + 1)
        else:
            prefix = "分镜" + str(p["shots"].index(owner) + 1)
    else:
        prefix = "历史角色"
        for index, name in enumerate(cast_names(p), 1):
            if character(name) == owner:
                prefix = f"角色{index}「{name}」"
                break
    return prefix + ("的" + fields[kind] if kind in fields else "")


def share_scene(p, shot, source):
    if shot not in p["shots"] or source not in p["shots"]:
        raise ValueError("请选择有效的分镜环境来源")
    if scene_identity(p, shot) != scene_identity(p, source):
        raise ValueError("地点和时间段相同的分镜才能共用环境")
    owner = shot if source == shot else scene_owner(p, source)
    p.setdefault("scene_sources", {})[shot] = owner


def group_scenes(p):
    groups = {}
    for s in p["shots"]:
        groups.setdefault(scene_identity(p, s), []).append(s)
    for members in groups.values():
        owner = max(members, key=lambda s: (status(p, s + ".plate") == "已确认",
                    s + ".plate" in p["nodes"], s + ".environment" in p["nodes"], -p["shots"].index(s)))
        for s in members:
            p.setdefault("scene_sources", {})[s] = owner


def dependencies(p, key):
    owner, kind = key.split(".")
    if kind in FIELDS:
        return []
    if kind == "profile":
        return [s + "." + f for s in p["shots"]
                if owner in map(character, value(p, s + ".cast")) for f in ("cast", "scene")]
    if kind == "sheet":
        return [owner + ".profile"]
    if kind == "environment":
        return [owner + ".scene"]
    if kind == "plate":
        return [owner + ".environment"]
    if kind in ("first", "last"):
        return [owner + ".scene", owner + ".cast", environment_key(p, owner + ".plate")] + [
            character(n) + ".sheet" for n in value(p, owner + ".cast")]
    if kind == "video":
        return [owner + "." + f for f in ("first", "scene", "dialogue")]
    if key == "final.film":
        return [s + ".video" for s in p["shots"]]
    raise ValueError("未知制作步骤")


def allowed(p, key):
    if key == "final.film" and p["shots"]:
        return
    owner, kind = key.split(".")
    if owner in p["shots"] and kind in (*FIELDS, "environment", "plate", "first", "last", "video"):
        return
    if kind in ("profile", "sheet") and owner in map(character, cast_names(p)):
        return
    raise ValueError("目标不属于当前分镜")


def stamps(p, key):
    return {d: len(p["nodes"].get(d, {}).get("versions", [])) for d in dependencies(p, key)}


def inputs_match(p, key, snapshot):
    saved = snapshot["inputs"]
    expected = set(dependencies(p, key))
    if key.endswith(".video") and snapshot.get("imported") is True:
        return not saved
    # Keep the actual provenance of videos made by the former two-frame workflow.
    legacy = expected | {key.split(".")[0] + ".last"} if key.endswith(".video") else expected
    if set(saved) not in (expected, legacy):
        return False
    for dep, revision in saved.items():
        versions = p["nodes"].get(dep, {}).get("versions", [])
        if not 1 <= revision <= len(versions) or versions[revision - 1]["value"] != versions[-1]["value"]:
            return False
    return True


def status(p, k):
    node = p["nodes"].get(k)
    if not node:
        return "待制作"
    current = node["versions"][-1]
    if k.split(".")[1] in STYLED_MEDIA and current.get("style", DEFAULT_STYLE) != p.get("style", DEFAULT_STYLE):
        return "上游已变更"
    if not inputs_match(p, k, current) or any(status(p, d) != "已确认" for d in current["inputs"]):
        return "上游已变更"
    return current["review"]


def ready(p, k):
    k = environment_key(p, k)
    allowed(p, k)
    for d in dependencies(p, k):
        if status(p, d) != "已确认":
            raise ValueError("请先审核上游" + label(p, d))


def version(p, k, v, note="", source="手动", prompt=None):
    k = environment_key(p, k)
    ready(p, k)
    kind = k.split(".")[1]
    node = p["nodes"].setdefault(k, {"versions": []})
    node["versions"].append(dict(value=clean(kind, v), inputs=stamps(p, k), at=now(),
                                 source=source, note=note, review="待审核", reviews=[], prompt=prompt))
    if kind in STYLED_MEDIA:
        node["versions"][-1]["style"] = p.get("style", DEFAULT_STYLE)


def review(p, k, approve, note):
    k = environment_key(p, k)
    allowed(p, k)
    if not (k.endswith(".video") and p["nodes"].get(k, {}).get("versions", [{}])[-1].get("imported")):
        ready(p, k)
    if k not in p["nodes"] or status(p, k) == "上游已变更":
        raise ValueError("请先按当前输入制作新版本")
    v = p["nodes"][k]["versions"][-1]
    v["review"] = "已确认" if approve else "需修改"
    v["reviews"].append(dict(at=now(), state=v["review"], note=note))


def restore(p, key, index, note=""):
    key = environment_key(p, key)
    allowed(p, key)
    versions = p["nodes"].get(key, {}).get("versions", [])
    if type(index) is not int or not 0 <= index < len(versions):
        raise ValueError("请选择有效的历史版本")
    previous = copy.deepcopy(versions[index])
    # Preserve the historical content, actual upstream references, and generation style.
    previous["value"] = clean(key.split(".")[1], previous["value"])
    previous.update(at=now(), source="历史回退", note=note, review="待审核", reviews=[], restored_from=index + 1)
    versions.append(previous)


def install_plan(p, obj):
    if p["shots"]:
        raise ValueError("已有分镜，请在新项目中重新拆镜")
    rows = obj["shots"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 15:
        raise ValueError("分镜数应为 1–15")
    validated = [{f: clean(f, row[f]) for f in FIELDS} for row in rows]
    if len(rows) >= 4 and sum(row["scene"]["seconds"] for row in validated) != 60:
        raise ValueError("分镜时长总和应为 60 秒")
    for i, row in enumerate(validated, 1):
        s = f"S{i:02}"
        p["shots"].append(s)
        for f, v in row.items():
            version(p, s + "." + f, v, source="分镜分析")


def visual(p, owner):
    scene = dict(value(p, owner + ".scene"))
    for line in value(p, owner + ".dialogue"):
        for field in ("plot", "first", "last"):
            scene[field] = scene[field].replace(line["text"], "")
    return scene


def execute(p, key, note, models, record_task=lambda _: None):
    import providers as api
    key = environment_key(p, key)
    if key == "plan":
        if p["shots"]:
            raise ValueError("已有分镜，请新建项目后重新拆镜")
        story = words(p["story"])
        instruction = skill("01-storyboard")
        for attempt in range(2):
            try:
                install_plan(p, api.plan_story(models["text"], instruction, story))
                return
            except (ValueError, KeyError, TypeError) as ex:
                if attempt:
                    raise ValueError(f"分镜结构校验失败：{ex}") from ex
                instruction += f"\n上次结构校验失败：{ex}。请按规范重新输出完整对象。"
    owner, kind = key.split(".")
    if kind == "last":
        raise ValueError("关键帧已改为仅生成首帧，请在首帧关键帧页面制作；历史尾帧仍保留在项目包中。")
    ready(p, key)
    model = models["text"]
    if kind in FIELDS:
        words(note)
        v = api.text(model, '按建议只修改当前字段，保持输入类型和字段结构。输出 {"value":修改后的字段值}。',
                     json.dumps(dict(value=value(p, key), story=p["story"], note=note), ensure_ascii=False))["value"]
        prompt = note
    elif kind in ("profile", "environment"):
        profile = kind == "profile"
        payload = dict(note=note)
        if profile:
            names = cast_names(p)
            name = next(n for n in names if character(n) == owner)
            payload.update(name=name, other_characters=[n for n in names if n != name], story=p["story"])
            payload["storyboard"] = [dict(shot=s, action=visual(p, s)["plot"])
                                     for s in p["shots"] if name in value(p, s + ".cast")]
        else:
            payload["scenes"] = [visual(p, owner)]
        if key in p["nodes"]:
            payload["previous"] = value(p, key)
            if profile and character_profiles.species(name):
                try:
                    character_profiles.validate_animal(name, payload["previous"])
                except ValueError:
                    del payload["previous"]
        prompt = skill("02-character" if profile else "03-environment")
        if profile:
            prompt += "\n结合 storyboard 中目标角色出场分镜的 action 与原文生成外观初稿。按角色归属提取稳定特征。"
            if character_profiles.species(name):
                payload["species"] = character_profiles.species(name)
                prompt += character_profiles.instruction(name)
        for attempt in range(2 if profile else 1):
            try:
                result = api.text(model, prompt, json.dumps(payload, ensure_ascii=False))
                v = character_profiles.description(name, result) if profile else result["description"]
                break
            except (ValueError, KeyError, TypeError) as exc:
                if not profile:
                    raise
                if attempt:
                    raise ValueError(f"角色设定校验失败：{exc}") from exc
                prompt += f"\n上次结果未通过校验：{exc}。请只输出当前角色的正确设定。"
    elif kind == "film":
        model = {"model": "FFmpeg"}
        prompt = "按分镜顺序拼接，720×1280，24 fps"
        v = api.join([value(p, s + ".video") for s in p["shots"]])
    elif kind == "video":
        model = models["video"]
        scene = visual(p, owner)
        prompt = f'以参考首帧为起始画面。{scene["plot"]}。由{scene["first"]}发展至{scene["last"]}，保持人物与场景连续。'
        dialogue = value(p, owner + ".dialogue")
        if dialogue:
            prompt += "音轨对白（不要显示字幕）：" + "；".join(x["speaker"] + "：" + x["text"] for x in dialogue)
        prompt += "\n" + note
        prompt += "\n整体视觉风格：" + p.get("style", DEFAULT_STYLE)
        v = api.video(model, prompt, [value(p, owner + ".first")],
                      scene["seconds"], record_task)
    else:
        model = models["image"]
        refs = []
        if kind == "sheet":
            prompt = value(p, owner + ".profile") + "。同一角色的全身正面、侧面、背面三视图，并排，白底。"
        elif kind == "plate":
            prompt = value(p, owner + ".environment") + "。只画无人空景，无人物、动物、肢体或人影。"
        else:
            scene = visual(p, owner)
            names = value(p, owner + ".cast")
            refs = [value(p, environment_key(p, owner + ".plate")), *[value(p, character(n) + ".sheet") for n in names]]
            prompt = (f'第一张图为环境，后续角色三视图按顺序对应{names}。分镜动作：{scene["plot"]}。'
                      f'只画{"开始" if kind == "first" else "结束"}时刻：{scene[kind]}。保持服装、脸型、光向。')
        prompt += "\n整体视觉风格：" + p.get("style", DEFAULT_STYLE) + "。无文字、字幕、气泡、水印。\n" + note
        v = api.image(model, prompt, refs, wide=kind == "sheet", record_task=record_task)
    version(p, key, v, note, model["model"], prompt)


def view(p):
    out = copy.deepcopy(p)
    out.setdefault("style", DEFAULT_STYLE)
    out["characters"] = {n: character(n) for n in cast_names(p)}
    out["scene_owners"] = {s: scene_owner(p, s) for s in p["shots"]}
    out["scene_candidates"] = {s: list(dict.fromkeys([s, *[out["scene_owners"][t] for t in p["shots"]
                               if scene_identity(p, s) == scene_identity(p, t)]])) for s in p["shots"]}
    out["scene_groups"] = {}
    for s, owner in out["scene_owners"].items():
        out["scene_groups"].setdefault(owner, []).append(s)
    for k, node in out["nodes"].items():
        node["status"] = status(p, k)
    return out
