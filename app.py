"""Single-user, localhost production workbench. Run with Python 3.11+."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import re
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import providers
import storage as st
import workflow as wf

worker = ThreadPoolExecutor(max_workers=1)


def state(c, pid):
    return dict(project=wf.view(st.get(c, pid)), jobs=[dict(r) for r in c.execute(
        "SELECT * FROM jobs WHERE project=? ORDER BY rowid DESC LIMIT 30", (pid,))])


def process(jid, project, key, note, models):
    try:
        with st.db() as c:
            c.execute("UPDATE jobs SET status='制作中' WHERE id=?", (jid,))
        def remote(task):
            with st.db() as c:
                c.execute("UPDATE jobs SET remote_id=? WHERE id=?", (task, jid))
        wf.execute(project, key, note, models, remote)
        with st.db() as c:
            st.save(c, project)
            c.execute("UPDATE jobs SET status='完成' WHERE id=?", (jid,))
    except Exception as exc:
        with st.db() as c:
            message = str(exc) or type(exc).__name__
            for service in models.values():
                if service.get("key"):
                    message = message.replace(service["key"], "***")
            c.execute("UPDATE jobs SET status='失败', message=? WHERE id=?", (message[:2400], jid))


def action(body):
    op = body["op"]
    with st.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if op == "settings":
            providers.configure(body["settings"])
            return providers.settings(public=True)
        if op == "new":
            p = wf.new(wf.words(body.get("title", "未命名漫剧")))
            st.save(c, p, fresh=True)
            return state(c, p["id"])
        p = st.get(c, body["id"])
        if p["revision"] != body["revision"]:
            raise ValueError("此项目有更新，请刷新后重试")
        if c.execute("SELECT 1 FROM jobs WHERE project=? AND status IN ('制作中','排队中')", (p["id"],)).fetchone():
            raise ValueError("本项目正在制作，请等待任务完成")
        k, note = wf.environment_key(p, body.get("target", "")), str(body.get("note", ""))
        if op == "save":
            p["title"] = wf.words(body["title"])
            if p["shots"] and p["story"] != body["story"]:
                raise ValueError("已拆镜的项目保留原文，新故事请新建项目")
            p["story"] = str(body["story"])
            if "style" in body:
                p["style"] = wf.words(body["style"])
        elif op == "scene-source":
            wf.share_scene(p, k, body["source"])
        elif op == "group-scenes":
            wf.group_scenes(p)
        elif op == "generate":
            if k == "plan":
                if p["shots"] or not p["story"].strip():
                    raise ValueError("请输入新项目的剧本原文")
            else:
                wf.ready(p, k)
            jid = uuid4().hex
            models = providers.selected()
            c.execute("INSERT INTO jobs VALUES(?,?,?,'排队中','','',?)",(jid,p["id"],k,wf.now()))
            c.commit()
            worker.submit(process,jid,p,k,note,models)
            return state(c, p["id"])
        elif op == "edit":
            if k.split(".")[-1] not in (*wf.FIELDS, "profile", "environment"):
                raise ValueError("仅支持编辑文字内容")
            wf.version(p, k, body["value"], note)
        elif op == "review":
            wf.review(p, k, body["approve"] is True, note)
        elif op == "restore":
            wf.restore(p, k, body["index"], note)
        else:
            raise ValueError("未知操作")
        st.save(c, p)
        return state(c, p["id"])


class Handler(BaseHTTPRequestHandler):
    def guard(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != host or self.headers.get("Origin") not in (None, "http://"+host):
            raise ValueError("请通过本机工作台访问")

    def reply(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(data)

    def stream(self, f, size, typ, download=None, media=False):
        start, end = 0, size-1
        partial = media and self.headers.get("Range")
        if partial:
            m = re.fullmatch(r"bytes=(\d+)-(\d*)",partial)
            if not m or int(m[1]) >= size:
                self.send_error(416)
                return
            start, end = int(m[1]), min(size-1, int(m[2] or size-1))
            if end < start:
                self.send_error(416)
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", typ)
        self.send_header("X-Content-Type-Options","nosniff")
        if media:
            self.send_header("Accept-Ranges","bytes")
        if partial:
            self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition",f'attachment; filename="{download}"')
        remaining = end-start+1
        self.send_header("Content-Length",str(remaining))
        self.end_headers()
        f.seek(start)
        while remaining:
            part = f.read(min(1024**2,remaining))
            if not part:
                break
            self.wfile.write(part)
            remaining -= len(part)

    def do_GET(self):
        try:
            self.guard()
            url = urlsplit(self.path)
            path = url.path
            if path == "/settings":
                return self.reply(providers.settings(public=True))
            if path.startswith("/media/") or path in ("/", "/ui.js", "/style.css"):
                media = path.startswith("/media/")
                f = st.asset(path[7:]) if media else st.ROOT/"static"/(path[1:] or "index.html")
                with f.open("rb") as src:
                    return self.stream(src, f.stat().st_size, mimetypes.guess_type(f.name)[0], media=media)
            with st.db() as c:
                if path == "/projects":
                    return self.reply([dict(r) for r in c.execute("""SELECT id, updated,
                        json_extract(body,'$.title') AS title, json_array_length(body,'$.shots') AS shots
                        FROM projects ORDER BY updated DESC,rowid DESC""")])
                pid = parse_qs(url.query)["id"][0]
                if path == "/project":
                    return self.reply(state(c, pid))
                p = st.get(c, pid)
            if path == "/package":
                with tempfile.TemporaryFile() as f:
                    st.pack(p, f)
                    return self.stream(f, f.tell(), "application/zip","comic-project.zip")
            if path == "/storyboard":
                return self.reply(dict(ratio=p["ratio"], style=p.get("style", wf.DEFAULT_STYLE), scene_sources={s:wf.scene_owner(p,s) for s in p["shots"]}, shots=[dict(id=s, **{f:wf.value(p,s+"."+f)
                                  for f in wf.FIELDS[:2]}) for s in p["shots"]],
                                  dialogue={s:wf.value(p,s+".dialogue") for s in p["shots"]}))
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.reply({"error":str(exc)},400)

    def do_POST(self):
        try:
            self.guard()
            n = int(self.headers.get("Content-Length", 0))
            if self.path == "/import" and 0 < n <= 2**31:
                with tempfile.TemporaryFile() as f:
                    while n:
                        chunk = self.rfile.read(min(n,1024**2))
                        if not chunk:
                            raise ValueError("上传中断")
                        f.write(chunk)
                        n -= len(chunk)
                    p = st.unpack(f)
                with st.db() as c:
                    return self.reply(state(c,p["id"]))
            if self.path != "/action" or self.headers.get_content_type() != "application/json" or not 0 < n <= 4*1024**2:
                raise ValueError("请求格式或大小无效")
            self.reply(action(json.loads(self.rfile.read(n))))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.reply({"error":str(exc)},400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    st.init()
    print(f"漫剧制作台  http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
