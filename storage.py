"""SQLite stores compact project documents and jobs. Binary assets are immutable."""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import workflow as wf

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("STUDIO_DATA", ROOT / "data")).resolve()
ASSETS = DATA / "media"


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(DATA / "studio.sqlite3", timeout=10)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init():
    ASSETS.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER, updated TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project TEXT, target TEXT, status TEXT,
          message TEXT, remote_id TEXT, created TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS active ON jobs(project) WHERE status IN ('排队中','制作中');
        """)
        c.execute("UPDATE jobs SET status='已中断', message='服务已重启，请检查远端任务号后决定是否重新生成' WHERE status IN ('排队中','制作中')")


def get(c, pid):
    r = c.execute("SELECT body FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ValueError("找不到项目")
    return json.loads(r[0])


def save(c, p, fresh=False):
    p["revision"] += 1
    p["updated"] = wf.now()
    args = (p["revision"], p["updated"], json.dumps(p, ensure_ascii=False), p["id"])
    if fresh:
        c.execute("INSERT INTO projects(revision,updated,body,id) VALUES(?,?,?,?)", args)
    elif not c.execute("UPDATE projects SET revision=?,updated=?,body=? WHERE id=? AND revision=?",
                       (*args, p["revision"] - 1)).rowcount:
        raise ValueError("项目已更新，请刷新后重试")


def asset(name):
    if not wf.MEDIA.fullmatch(name):
        raise ValueError("素材文件名无效")
    return ASSETS / name


def persist(path, ext):
    with open(path, "rb") as f:
        head = f.read(12)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            actual = "png"
        elif head.startswith(b"\xff\xd8\xff"):
            actual = "jpg"
        elif head[:4] == b"RIFF" and head[8:] == b"WEBP":
            actual = "webp"
        elif head[4:8] == b"ftyp":
            actual = "mp4"
        else:
            raise ValueError("生成器返回了无法识别的媒体文件")
        if (ext == "mp4") != (actual == "mp4"):
            raise ValueError("生成器返回了错误的媒体类型")
        f.seek(0)
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    name = digest + "." + actual
    os.replace(path, asset(name))
    return name


def media_refs(p):
    return {v["value"] for k, node in p["nodes"].items()
            if k.split(".")[1] in ("sheet", "plate", "first", "last", "video", "film")
            for v in node["versions"]}


def pack(p, target):
    with zipfile.ZipFile(target, "w") as z:
        z.writestr("project.json", json.dumps(p, ensure_ascii=False, indent=2))
        for name in sorted(media_refs(p)):
            z.write(asset(name), "media/" + name)


def validate(p):
    if p.get("schema_version") != 1 or p.get("ratio") != "9:16":
        raise ValueError("需要版本 1 的 9:16 项目包")
    wf.words(p["title"])
    wf.words(p.get("style", wf.DEFAULT_STYLE))
    if not isinstance(p["story"], str) or not isinstance(p["shots"], list) or not isinstance(p["nodes"], dict):
        raise ValueError("项目结构不完整")
    if len(p["shots"]) not in (0, 4, 5, 6) or p["shots"] != [f"S{i+1:02}" for i in range(len(p["shots"]))]:
        raise ValueError("无效分镜顺序")
    for s in p["shots"]:
        for field in wf.FIELDS:
            if s + "." + field not in p["nodes"]:
                raise ValueError("缺少分镜字段")
    for k, n in p["nodes"].items():
        if not re.fullmatch(r"(S0[1-6]\.(scene|cast|dialogue|environment|plate|first|last|video)|[0-9a-f]{24}\.(profile|sheet)|final\.film)", k):
            raise ValueError("无效节点标识")
        if k.startswith("S") and k.split(".")[0] not in p["shots"]:
            raise ValueError("镜头不存在")
        if not isinstance(n["versions"], list) or not n["versions"]:
            raise ValueError("缺少节点版本")
        for v in n["versions"]:
            if "style" in v:
                wf.words(v["style"])
            wf.clean(k.split(".")[1], v["value"])
            if (v["review"] not in ("待审核", "已确认", "需修改") or not isinstance(v["reviews"], list)
                    or not isinstance(v["inputs"], dict)):
                raise ValueError("版本记录无效")
            for dep, rev in v["inputs"].items():
                if dep not in p["nodes"] or type(rev) != int or not 1 <= rev <= len(p["nodes"][dep]["versions"]):
                    raise ValueError("上游版本引用无效")
    wf.view(p)


def unpack(src):
    with zipfile.ZipFile(src) as z:
        entries = z.infolist()
        if (len(entries) > 10000 or len(set(z.namelist())) != len(entries)
                or sum(i.file_size for i in entries) > 2**31
                or z.getinfo("project.json").file_size > 16 * 1024**2):
            raise ValueError("项目包过大或包含重复文件")
        p = json.loads(z.read("project.json"))
        validate(p)
        names = media_refs(p)
        if set(z.namelist()) != {"project.json", *("media/" + n for n in names)}:
            raise ValueError("项目包素材清单不匹配")
        with tempfile.TemporaryDirectory(dir=DATA) as stage:
            for name in names:
                with z.open("media/" + name) as source, open(Path(stage) / name, "wb") as out:
                    import shutil
                    shutil.copyfileobj(source, out)
                with open(Path(stage) / name, "rb") as f:
                    if hashlib.file_digest(f, "sha256").hexdigest() != name.split(".")[0]:
                        raise ValueError("素材校验失败：" + name)
            for name in names:
                os.replace(Path(stage) / name, asset(name))
        p.update(id=uuid4().hex, revision=0)
        with db() as c:
            save(c, p, fresh=True)
        return p
