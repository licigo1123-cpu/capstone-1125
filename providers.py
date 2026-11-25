"""Model adapters and FFmpeg output. API credentials live outside project documents."""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import storage as st
import storyboard

H3 = Path(os.getenv("H3_RUNTIME", st.ROOT.parent / "h3-runtime")).resolve()
IMAGE = Path(os.getenv("H3_IMAGE_RUNTIME", st.ROOT.parent / "h3-image-runtime")).resolve()
FF = os.getenv("FFMPEG") or shutil.which("ffmpeg") or str(H3 / ".venv/bin/ffmpeg")
DEFAULTS = {
    "text": {"local": ("http://127.0.0.1:11434", "qwen3.5:4b"), "api": ("https://api.openai.com/v1", "gpt-4o-mini")},
    "image": {"local": ("", "h3-image-mlx"), "api": ("https://api.openai.com/v1", "gpt-image-1")},
    "video": {"local": ("", "MiniMax-H3 MLX"), "api": ("https://api.minimax.io", "MiniMax-H3")},
}


def settings(public=False):
    path = st.DATA / "connections.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    result = {}
    for kind, choices in DEFAULTS.items():
        old = config.get(kind, {})
        row = {"mode": old.get("mode", "local")}
        for mode, (url, model) in choices.items():
            d = {"url": url, "model": model, **old.get(mode, {})}
            if public:
                d["has_key"] = bool(d.pop("key", ""))
            row[mode] = d
        result[kind] = row
    return result


def configure(new):
    cfg = settings()
    for kind, entry in cfg.items():
        mode = new[kind]["mode"]
        if mode not in ("api", "local"):
            raise ValueError("模型来源无效")
        entry["mode"] = mode
        for provider in DEFAULTS[kind]:
            d = new[kind].get(provider)
            if not d:
                continue
            url = d["url"].strip().rstrip("/")
            if (provider == "api" or kind == "text") and urlsplit(url).scheme not in ("http", "https"):
                raise ValueError("需要有效的 HTTP(S) 基础地址")
            old = entry[provider]
            if old["url"] != url:
                old.pop("key", None)
            old.update(url=url, model=d["model"].strip())
            if d.get("key"):
                old["key"] = d["key"]
    with tempfile.NamedTemporaryFile(mode="w", dir=st.DATA, delete=False) as f:
        json.dump(cfg, f)
    os.replace(f.name, st.DATA / "connections.json")


def selected():
    return {k: {**d[d["mode"]], "mode": d["mode"]} for k, d in settings().items()}


def request(url, body=None, key="", typ="application/json", binary=False, extra_headers=None):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("服务地址无效")
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body, ensure_ascii=False).encode()
    headers = {"Content-Type": typ, **(extra_headers or {})}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=600) as r:
            if binary:
                return r.read()
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = str(e.reason)
        try:
            err = json.load(e).get("error")
            msg = err.get("message", msg) if isinstance(err, dict) else str(err or msg)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"模型接口 HTTP {e.code}：{msg[:500]}") from e


def local_text(cfg, instruction, material):
    body = dict(model=cfg["model"],
                messages=[{"role":"system", "content":instruction}, {"role":"user", "content":material}],
                stream=False, think=False, keep_alive=0,
                options=dict(temperature=0.1, num_ctx=8192, num_predict=4096))
    response = request(cfg["url"] + "/api/chat", body)
    if response.get("done_reason") == "length":
        raise ValueError("Qwen 输出达到长度上限，请缩短原文后重试")
    raw = response.get("message", {}).get("content")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Qwen 没有返回正文")
    return raw


def plan_story(cfg, instruction, material):
    if cfg["mode"] != "local":
        return text(cfg, instruction, material)
    # Remove the JSON output contract. Retry messages retain only the validation feedback.
    feedback = instruction.partition("上次结构校验失败：")[2]
    prompt = storyboard.INSTRUCTION
    if feedback:
        prompt += "\n上次表格校验失败：" + feedback
    return storyboard.parse(local_text(cfg, prompt, material))


def text(cfg, instruction, material):
    messages = [{"role":"system", "content":instruction}, {"role":"user", "content":material}]
    body = dict(model=cfg["model"], messages=messages)
    if cfg["mode"] == "local":
        raw = local_text(cfg, instruction, material)
    else:
        body["response_format"] = {"type":"json_object"}
        raw = request(cfg["url"] + "/chat/completions", body, cfg.get("key"))["choices"][0]["message"]["content"]
    return json.loads(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip()))


def run(args, cwd=None, capture=False):
    if not Path(str(args[0])).is_file() and not shutil.which(str(args[0])):
        raise ValueError("找不到运行程序：" + str(args[0]))
    r = subprocess.run([str(a) for a in args], cwd=cwd, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, env={**os.environ, "PATH":str(H3/".venv/bin")+os.pathsep+os.getenv("PATH","")})
    if r.returncode:
        raise RuntimeError(r.stdout[-2200:].decode(errors="replace"))
    return r.stdout.decode(errors="replace") if capture else None


def data_url(name):
    fmt = {"jpg":"jpeg", "png":"png", "webp":"webp"}[name.split(".")[-1]]
    return "data:image/" + fmt + ";base64," + base64.b64encode(st.asset(name).read_bytes()).decode()


def multipart(fields, refs):
    boundary = uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name in refs:
        mime = {"png":"image/png","jpg":"image/jpeg","webp":"image/webp"}[name.split(".")[-1]]
        out.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        out.extend(st.asset(name).read_bytes())
        out.extend(b"\r\n")
    out.extend(f"--{boundary}--\r\n".encode())
    return bytes(out), "multipart/form-data; boundary=" + boundary


def muse_image(cfg, prompt, refs, wide, record_task):
    if len(refs) > 9 or sum(st.asset(name).stat().st_size for name in refs) > 11 * 1024**2:
        raise ValueError("Muse 最多支持 9 张参考图，原始文件总计不超过 11 MiB")
    base = cfg["url"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    body = dict(model=cfg["model"], prompt=prompt, n=1, aspect_ratio="3:2" if wide else "9:16")
    if refs:
        body["image_urls"] = [data_url(name) for name in refs]
    response = request(base + "/images/generations", body, cfg.get("key"),
                       extra_headers={"Idempotency-Key": uuid4().hex})
    task = str(response["data"][0]["task_id"])
    record_task(task)
    for _ in range(180):
        result = request(base + "/tasks/" + quote(task, safe=""), key=cfg.get("key"))["data"]
        if result["status"] == "completed":
            url = result["result"]["images"][0]["url"]
            if isinstance(url, list):
                url = url[0]
            return {"url": url}
        if result["status"] in ("failed", "cancelled", "canceled"):
            raise ValueError(f"Muse 任务 {task} 失败：{result.get('error') or result['status']}")
        time.sleep(2)
    raise ValueError(f"Muse 任务 {task} 查询超时，任务号已保存，请先核对原任务结果")


def image(cfg, prompt, refs, wide=False, record_task=lambda _: None):
    with tempfile.TemporaryDirectory(dir=st.DATA) as td:
        out = Path(td) / "image.png"
        if cfg["mode"] == "local":
            w, h = (1536,1024) if wide else (576,1024)
            cmd = [IMAGE/".venv/bin/h3-image-mlx","generate","--models",IMAGE/"models","--dit-mode","stream",
                   "--width",w,"--height",h,"--prompt",prompt,"--output",out]
            for name in refs:
                cmd.extend(["--reference", st.asset(name)])
            run(cmd)
        else:
            if not wide:
                prompt += "。纵向构图，重要人物及动作放在画幅中央的 9:16 安全区。"
            if cfg["model"].lower().startswith("muse-image"):
                output = muse_image(cfg, prompt, refs, wide, record_task)
            else:
                fields = dict(model=cfg["model"], prompt=prompt, size="1536x1024" if wide else "1024x1536", n=1)
                if refs:
                    data, typ = multipart(fields, refs)
                    res = request(cfg["url"]+"/images/edits", data, cfg.get("key"), typ)
                else:
                    res = request(cfg["url"]+"/images/generations", fields, cfg.get("key"))
                output = res["data"][0]
            raw = base64.b64decode(output["b64_json"],validate=True) if output.get("b64_json") else request(output["url"],binary=True)
            src = Path(td)/"input"
            src.write_bytes(raw)
            w,h = (1536,1024) if wide else (576,1024)
            run([FF,"-v","error","-i",src,"-vf",f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1","-frames:v",1,out])
        return st.persist(out, "png")


def video(cfg, prompt, refs, seconds, record_task):
    with tempfile.TemporaryDirectory(dir=st.DATA) as td:
        out = Path(td)/"shot.mp4"
        if cfg["mode"] == "local":
            ckpt = H3/"models/MiniMax-H3/FL2VA"
            run([H3/".venv/bin/python","scripts/generate.py",prompt,"--checkpoint",ckpt,"--transformer",ckpt/"transformer",
                 "--turbo-lora","models/turbo/minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors",
                 "--sigma-shift-video",6,"--sigma-shift-audio",3,"--low-memory","--stream-blocks",
                 "--stream-block-group-size",1,"--no-block-cache","--resolution","288x512","--aspect",9,16,
                 "--image",st.asset(refs[0]),"--anchor","first","--image",st.asset(refs[1]),"--anchor","last",
                 "--duration",seconds,"--steps",5,"--memory-limit-gb",os.getenv("H3_MEMORY_GB","20"),
                 "--require-muxed-mp4","--output",out], H3)
        else:
            content = [{"type":"text","text":prompt}]
            for role, name in zip(("first_frame","last_frame"), refs):
                content.append({"type":"image_url","role":role,"image_url":{"url":data_url(name)}})
            task = request(cfg["url"]+"/v2/video_generation", dict(model=cfg["model"], content=content,
                           duration=seconds, resolution="768P", ratio="9:16"), cfg.get("key"))["task_id"]
            record_task(str(task))
            for _ in range(450):
                res = request(cfg["url"]+"/v2/query/video_generation/"+quote(str(task),safe=""),key=cfg.get("key"))["task"]
                if res["status"] == "succeeded":
                    out.write_bytes(request(res["content"]["url"], binary=True))
                    break
                if res["status"] in ("failed","canceled","cancelled","expired"):
                    raise ValueError(f'H3 任务 {task}：{res.get("error") or res["status"]}')
                time.sleep(4)
            else:
                raise ValueError(f"H3 查询超时，远端任务号 {task}")
        return st.persist(out, "mp4")


def join(names):
    if not names:
        raise ValueError("缺少分镜视频")
    with tempfile.TemporaryDirectory(dir=st.DATA) as td:
        parts = []
        for i, name in enumerate(names):
            path = st.asset(name)
            # FFmpeg prints the input streams even when no output is selected.
            probe = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True)
            has_audio = b"Audio:" in probe.stderr
            args = [FF,"-v","error","-i",path]
            if not has_audio:
                args.extend(["-f","lavfi","-i","anullsrc=r=48000:cl=stereo"])
            run([*args,"-map","0:v:0","-map","0:a:0" if has_audio else "1:a:0",
                 "-vf","scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
                 "-af","apad","-shortest","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                 "-c:a","aac","-ar",48000,"-ac",2,f"{i}.mp4"],td)
            parts.append(f"file '{i}.mp4'\n")
        Path(td,"timeline.txt").write_text("".join(parts))
        out = Path(td,"film.mp4")
        run([FF,"-v","error","-f","concat","-safe",1,"-i","timeline.txt","-c","copy","-movflags","+faststart",out],td)
        return st.persist(out, "mp4")
