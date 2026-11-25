import copy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import app
import providers
import storage as st
import workflow as wf
import storyboard


def plan():
    return {"shots": [{"scene":dict(place="花店",time="黄昏",plot="小猫走到花架旁", first="门边蹲坐",
                                    last="花架前停下",seconds=15), "cast":["小猫"],
                       "dialogue":[{"speaker":"旁白","text":"花店要关门了。"}]} for _ in range(4)]}


class Request:
    """Exercise BaseHTTPRequestHandler over a memory transport when loopback is unavailable."""
    def __init__(self, raw):
        self.src = BytesIO(raw)
        self.out = BytesIO()

    def makefile(self, *args):
        return self.src

    def sendall(self, b):
        self.out.write(b)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        patcher = patch.multiple(st, DATA=root, ASSETS=root/"media")
        patcher.start()
        self.addCleanup(patcher.stop)
        st.init()

    def http(self, method, path, body=b"", typ="application/json", headers=()):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = Request((f"{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:8767\r\nContent-Type: {typ}\r\n"
                       f"Content-Length: {len(body)}\r\n"+"".join(x+"\r\n" for x in headers)+"\r\n").encode()+body)
        with patch.object(app.Handler, "log_message"):
            app.Handler(req, ("127.0.0.1",12345), type("Server",(),{"server_port":8767})())
        head, data = req.out.getvalue().split(b"\r\n\r\n",1)
        return int(head.split()[1]), data

    def test_http_save_import_settings_and_revision_conflict(self):
        self.assertIn("剧本".encode(), self.http("GET","/ui.js")[1])
        _, raw = self.http("POST","/action",{"op":"new"})
        p = json.loads(raw)["project"]
        cmd = dict(op="save",id=p["id"],revision=p["revision"],title="小猫",story="小猫推开了花店的门。")
        self.assertEqual(self.http("POST","/action",cmd)[0],200)
        self.assertEqual(self.http("POST","/action",cmd)[0],400)
        cfg = providers.settings(public=True)
        cfg["video"]["api"]["key"]="TEST-ONLY-FICTIONAL"
        raw = self.http("POST","/action",dict(op="settings",settings=cfg))[1]
        self.assertNotIn(b"TEST-ONLY",raw)
        self.assertTrue(json.loads(raw)["video"]["api"]["has_key"])
        code, archive = self.http("GET","/package?id="+p["id"])
        self.assertEqual(code,200)
        self.assertNotIn(b"TEST-ONLY",archive)
        imported = json.loads(self.http("POST","/import",archive,"application/zip")[1])["project"]
        self.assertNotEqual(imported["id"],p["id"])
        self.assertEqual(imported["story"],cmd["story"])
        self.assertEqual(len(json.loads(self.http("GET","/projects")[1])),2)

    def test_async_plan_repair_and_json(self):
        p=app.action({"op":"new"})["project"]
        p=app.action(dict(op="save",id=p["id"],revision=p["revision"],title="片名",story="一只小猫"))["project"]
        invalid=plan()
        invalid["shots"][0]["scene"]["seconds"]=12
        with patch.object(providers,"plan_story",side_effect=[invalid,plan()]) as model:
            app.action(dict(op="generate",id=p["id"],revision=p["revision"],target="plan"))
            for _ in range(100):
                time.sleep(.01)
                with st.db() as c:
                    latest=app.state(c,p["id"])
                if latest["jobs"][0]["status"]=="完成":
                    break
            self.assertEqual(latest["jobs"][0]["status"],"完成")
            self.assertEqual(model.call_count,2)
        bundle=json.loads(self.http("GET","/storyboard?id="+p["id"])[1])
        self.assertNotIn("dialogue",bundle["shots"][0])
        self.assertEqual(bundle["dialogue"]["S01"][0]["speaker"],"旁白")

    def test_dependencies_revision_and_review(self):
        p=wf.new()
        wf.install_plan(p,plan())
        self.assertRaises(ValueError,wf.ready,p,"S01.first")
        for s in p["shots"]:
            for k in wf.FIELDS:
                wf.review(p,s+"."+k,True,"通过")
        char=wf.character("小猫")
        for k in [char+".profile",char+".sheet","S01.environment","S01.plate","S01.first","S01.last","S01.video"]:
            kind=k.split(".")[1]
            v="a"*64+(".mp4" if kind=="video" else ".png")
            wf.version(p,k,"环境与外观" if kind in ("profile","environment") else v)
            wf.review(p,k,True,"通过")
        wf.version(p,"S01.dialogue",[])
        self.assertEqual(wf.status(p,"S01.first"),"已确认")
        self.assertEqual(wf.status(p,"S01.video"),"上游已变更")
        wf.version(p,char+".profile","新设定")
        self.assertEqual(wf.status(p,"S01.first"),"上游已变更")
        self.assertRaises(ValueError,wf.review,p,"S01.first",True,"")
        self.assertEqual(len(p["nodes"][char+".profile"]["versions"]),2)

    def shot_table(self, count=4):
        return "| 镜号 | 地点 | 时段 | 出镜角色 | 动作与运镜 | 开始画面 | 结束画面 | 对白 |\n" + \
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n" + "\n".join(
                f"| {i} | 花店 | 黄昏 | 小猫、店主 | 小猫走到花架旁 | 门边蹲坐 | 花架前停下 | 店主：‘欢迎’；旁白：花店要关门了。 |"
                for i in range(1,count+1))

    def test_local_table_plan_from_raw_model_response(self):
        cfg=dict(mode="local", model="qwen3.5:4b", url="http://127.0.0.1:11434")
        p=wf.new()
        p["story"]="一只小猫进入了花店。"
        with patch.object(providers,"request",return_value={"message":{"content":self.shot_table()}}) as req:
            wf.execute(p,"plan","",{"text":cfg})
        sent=req.call_args.args[1]
        self.assertEqual(sent["messages"][1]["content"],p["story"])
        self.assertNotIn("format",sent)
        self.assertIn("不要输出 JSON",sent["messages"][0]["content"])
        self.assertEqual(len(p["shots"]),4)
        self.assertEqual(sum(wf.value(p,s+".scene")["seconds"] for s in p["shots"]),60)
        self.assertEqual(wf.value(p,"S01.dialogue")[1]["speaker"],"旁白")
        self.assertEqual(wf.value(p,"S01.cast"),["小猫","店主"])
        st.validate(p)

    def test_table_lengths_and_invalid_rows(self):
        for count in (4,5,6):
            p=wf.new()
            wf.install_plan(p,storyboard.parse(self.shot_table(count)))
            self.assertEqual(sum(wf.value(p,s+".scene")["seconds"] for s in p["shots"]),60)
        for text in (self.shot_table(3),self.shot_table().replace("| 2 |","| 3 |"),
                     self.shot_table().replace("门边蹲坐 |", "门边 | 蹲坐 |")):
            self.assertRaises(ValueError,storyboard.parse,text)

    def test_dialogue_variants_and_missing_speaker(self):
        for text in ("无", "（无对白）", "无台词", "无（仅环境音）", "None", "—", ""):
            self.assertEqual(storyboard.dialogue_lines(text),[],text)
        for text in ('“等我一下！”', "等我一下！", "店主：", '店主：“”', "：等我一下！"):
            lines=storyboard.dialogue_lines(text)
            self.assertEqual(lines,[dict(speaker="待确认",text=storyboard.unquote(text))])
        self.assertEqual(storyboard.dialogue_lines('“店主：欢迎；小猫：喵。”'),[
            dict(speaker="店主",text="欢迎"),dict(speaker="小猫",text="喵。")])
        self.assertEqual(storyboard.dialogue_lines("店主：‘欢迎’<br/>旁白：小猫停了下来。"),[
            dict(speaker="店主",text="欢迎"),dict(speaker="旁白",text="小猫停了下来。")])

    def test_fifth_shot_unlabelled_dialogue_preserves_plan(self):
        table=self.shot_table(5)
        last=table.splitlines()[-1]
        for dialogue, expected in [("（无对白）", []), ("明天还来这里。", [dict(speaker="待确认",text="明天还来这里。")])]:
            row=last.rsplit("|",2)
            modified=table.rsplit("\n",1)[0]+"\n"+row[0]+"| "+dialogue+" |"
            p=wf.new()
            wf.install_plan(p,storyboard.parse(modified))
            self.assertEqual(len(p["shots"]),5)
            self.assertEqual(wf.value(p,"S05.dialogue"),expected)
            self.assertEqual(wf.status(p,"S05.dialogue"),"待审核")
            st.validate(p)

    def test_bad_local_output_retries_without_partial_plan(self):
        cfg=dict(mode="local", model="qwen3.5:4b", url="http://127.0.0.1:11434")
        p=wf.new()
        p["story"]="小猫遇到店主。"
        responses=[{"message":{"content":'{"shots":[{scene: "坏 JSON"}]}'}},
                   {"message":{"content":self.shot_table()}}]
        with patch.object(providers,"request",side_effect=responses) as req:
            wf.execute(p,"plan","",{"text":cfg})
        self.assertEqual(len(p["shots"]),4)
        self.assertIn("上次表格校验失败",req.call_args.args[1]["messages"][0]["content"])
        p=wf.new()
        p["story"]="小猫遇到店主。"
        with patch.object(providers,"request",return_value={"done_reason":"length","message":{"content":self.shot_table()}}):
            self.assertRaisesRegex(ValueError,"长度上限",wf.execute,p,"plan","",{"text":cfg})
        self.assertEqual((p["shots"],p["nodes"]),([],{}))

    def test_api_plan_retains_json_protocol(self):
        cfg=dict(mode="api",model="test",url="https://example.test/v1")
        with patch.object(providers,"request",return_value={"choices":[{"message":{"content":json.dumps(plan())}}]}) as req:
            self.assertEqual(providers.plan_story(cfg,"instruction","story"),plan())
        self.assertEqual(req.call_args.args[1]["response_format"],{"type":"json_object"})

    def test_minimax_and_local_video_bind_both_anchors(self):
        keys=["a"*64+".png","b"*64+".png"]
        for k in keys:
            st.asset(k).write_bytes(k.encode())
        mp4=b"\x00\x00\x00\x18ftypisom"+b"test"
        task=[]
        with patch.object(providers,"request",side_effect=[{"task_id":"m1"},{"task":{"status":"succeeded","content":{"url":"https://cdn.example/clip"}}},mp4]) as req:
            result=providers.video(dict(mode="api",model="MiniMax-H3",url="https://example.test"),"剧情",keys,12,task.append)
            body=req.call_args_list[0].args[1]
            self.assertEqual([x["role"] for x in body["content"][1:]],["first_frame","last_frame"])
            self.assertNotEqual(body["content"][1]["image_url"],body["content"][2]["image_url"])
            self.assertEqual((task,body["ratio"],body["duration"]),(["m1"],"9:16",12))
            self.assertTrue(st.asset(result).exists())
        def fake_run(cmd,cwd):
            self.assertEqual([cmd[i+1] for i,x in enumerate(cmd) if x=="--anchor"],["first","last"])
            self.assertEqual([cmd[i+1] for i,x in enumerate(cmd) if x=="--image"],list(map(st.asset,keys)))
            Path(cmd[-1]).write_bytes(mp4)
        with patch.object(providers,"run",side_effect=fake_run):
            providers.video({"mode":"local"},"剧情",keys,12,task.append)

    def test_full_production_export_and_archive(self):
        p=wf.new("测试短片")
        p["story"]="小猫在花店等到了主人。"
        wf.install_plan(p,plan())
        models={k:{"model":"test double"} for k in ("text","image","video")}
        char=wf.character("小猫")
        for s in p["shots"]:
            for f in wf.FIELDS:
                wf.review(p,s+"."+f,True,"")
        def make_image(cfg,prompt,refs,wide=False):
            if "第一张图" in prompt:
                self.assertEqual(len(refs),2)
                self.assertNotIn("花店要关门了",prompt)
            img=st.DATA/"fixture.png"
            providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=green:s=90x160","-frames:v",1,img])
            return st.persist(img,"png")
        def make_video(cfg,prompt,refs,seconds,remote):
            self.assertEqual(len(refs),2)
            src=st.DATA/"clip.mp4"
            providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=green:s=90x160:r=24",
                           "-f","lavfi","-i","sine=frequency=330","-t",seconds,"-c:v","libx264","-c:a","aac",src])
            return st.persist(src,"mp4")
        order=[char+".profile",char+".sheet"]+[s+"."+k for s in p["shots"] for k in ("environment","plate","first","last","video")]
        with patch.object(providers,"text",return_value={"description":"角色或环境描述"}),patch.object(providers,"image",side_effect=make_image),patch.object(providers,"video",side_effect=make_video):
            for k in order:
                wf.execute(p,k,"测试意见",models)
                wf.review(p,k,True,"同意使用")
            wf.execute(p,"final.film","",models)
        film=st.asset(wf.value(p,"final.film"))
        info=subprocess.run([providers.FF,"-i",str(film)],capture_output=True,text=True).stderr
        self.assertIn("720x1280",info)
        self.assertIn("Audio: aac",info)
        self.assertRegex(info,r"Duration: 00:01:00\.")
        package=BytesIO()
        st.pack(p,package)
        out=st.unpack(package)
        self.assertNotEqual(p["id"],out["id"])
        self.assertEqual(p["nodes"],out["nodes"])
        code,part=self.http("GET","/media/"+film.name,headers=["Range: bytes=0-11"])
        self.assertEqual((code,part),(206,film.read_bytes()[:12]))
        with ZipFile(package) as original, BytesIO() as bad:
            with ZipFile(bad,"w") as z:
                for item in original.namelist():
                    z.writestr(item,b"corrupt" if item.endswith(".mp4") else original.read(item))
            self.assertRaises(ValueError,st.unpack,bad)
