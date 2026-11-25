"""Species-aware generation contracts and validation for character descriptions."""
import json
import re


ANIMAL_FIELDS = ("体型", "毛色与花纹", "头部特征", "四肢与尾巴", "服饰与配饰")
HUMAN_FEATURES = re.compile(r"少女|女孩|男孩|鹅蛋脸|肩窄|窄肩|腰细|细腰|皮肤|红晕|长直发|黑色长发|刘海|手腕|手指|画夹|百褶裙")
SCENE_FEATURES = re.compile(r"光影处理|逆光|镜头|黄昏|夜晚|呼应原文|情绪低落期|专注创作期|温暖觉醒期")


def species(name):
    match = re.search(r"(猫|狗|犬|兔|狐狸|鹦鹉|鸟|仓鼠)(?:[（(][^）)]*[）)])?$", name)
    if not match:
        return ""
    return {"犬": "狗"}.get(match[1], match[1])


def validate_animal(name, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("动物角色描述为空")
    match = HUMAN_FEATURES.search(text) or SCENE_FEATURES.search(text)
    if match:
        raise ValueError(f"动物角色「{name}」混入无关特征：{match[0]}")
    if species(name) == "猫" and re.search(r"(?:1[2-9]\d|2\d\d)\s*(?:cm|厘米)", text, re.I):
        raise ValueError("猫的体型混入了人类身高，请按真实猫的体型描述")
    if "三花" in name and not ("黑" in text and "白" in text and ("橘" in text or "橙" in text)):
        raise ValueError("三花猫需要明确黑、白、橘（橙）三色毛块")
    return text.strip()


def instruction(name):
    contract = {"name": name, "species": species(name),
                "attributes": {field: "仅填写该动物自身的稳定可见特征" for field in ANIMAL_FIELDS}}
    prompt = ("\n目标物种已确定为" + species(name) + "。按该动物的解剖结构和体型生成。"
              "采用动物设定字段，四肢、口鼻、耳朵、眼睛、毛发或羽毛及尾巴分别描述。"
              "服饰与配饰仅依据原文明确穿戴于该动物的物品填写，原文未提到时填空字符串。"
              "原文中人类的外貌、服装及创作行为分别属于对应人类角色。"
              "本次输出严格采用以下 JSON，attributes 仅包含这五个字段，前三项必须有具体内容："
              + json.dumps(contract, ensure_ascii=False))
    if "三花" in name:
        prompt += "\n三花猫的毛色写黑、白、橘三色的不规则斑块，物种字段仍写猫。"
    return prompt


def description(name, result):
    if not isinstance(result, dict) or result.get("name") != name:
        raise ValueError(f"角色设定返回的姓名与当前角色「{name}」不一致")
    animal = species(name)
    if not animal:
        text = result.get("description")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("角色描述为空")
        return text.strip()
    if result.get("species") != animal:
        raise ValueError(f"角色「{name}」的物种必须是{animal}")
    attributes = result.get("attributes")
    if not isinstance(attributes, dict) or set(attributes) != set(ANIMAL_FIELDS):
        raise ValueError("动物角色必须按指定的五个 attributes 字段返回描述")
    if set(result) != {"name", "species", "attributes"}:
        raise ValueError("动物角色输出混入了额外字段")
    for field in ANIMAL_FIELDS:
        if not isinstance(attributes[field], str) or (field in ANIMAL_FIELDS[:3] and not attributes[field].strip()):
            raise ValueError(f"动物角色的{field}字段无效")
    text = f"物种：{animal}。" + "".join(f"{field}：{attributes[field].strip()}。"
                                             for field in ANIMAL_FIELDS if attributes[field].strip())
    return validate_animal(name, text)
