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
import character_profiles


def plan():
    return {"shots": [{"scene":dict(place="花店",time="黄昏",plot="小猫走到花架旁", first="门边蹲坐",
                                    last="花架前停下",seconds=15), "cast":["小猫"],
                       "dialogue":[{"speaker":"旁白","text":"花店要关门了。"}]} for _ in range(4)]}


def cat_profile(name="小猫"):
    return dict(name=name,species="猫",attributes={"体型":"小型猫，四足，体态匀称",
                "毛色与花纹":"黑、白、橘三色不规则毛块" if "三花" in name else "橘白色短毛",
                "头部特征":"圆脸，绿色眼睛，三角形耳朵", "四肢与尾巴":"四肢纤细，长尾巴",
                "服饰与配饰":"蓝色项圈"})


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
        self.assertEqual(bundle["style"],wf.DEFAULT_STYLE)
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

    def test_upstream_errors_identify_numbered_shots_characters_and_scenes(self):
        p=wf.new()
        wf.install_plan(p,plan())
        char=wf.character("小猫")
        self.assertRaisesRegex(ValueError,"请先审核上游角色1「小猫」的设定卡",wf.ready,p,char+".sheet")
        self.assertRaisesRegex(ValueError,"请先审核上游分镜1的场景与剧情",wf.ready,p,"S01.first")
        wf.review(p,"S01.scene",True,"")
        wf.review(p,"S01.cast",True,"")
        self.assertRaisesRegex(ValueError,"请先审核上游场景1的环境参考图",wf.ready,p,"S01.first")
        wf.group_scenes(p)
        self.assertEqual(wf.label(p,"S03.plate"),"场景1的环境参考图")
        with st.db() as c:
            st.save(c,p,fresh=True)
        code,body=self.http("POST","/action",dict(op="generate",id=p["id"],revision=p["revision"],target=char+".sheet"))
        self.assertEqual(code,400)
        text=json.loads(body)["error"]
        self.assertIn("角色1「小猫」",text)
        self.assertNotIn(char,text)
        self.assertNotIn(".profile",text)

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
        for count in range(1,16):
            p=wf.new()
            wf.install_plan(p,storyboard.parse(self.shot_table(count)))
            self.assertEqual(len(p["shots"]),count)
            st.validate(p)
            self.assertEqual(sum(wf.value(p,s+".scene")["seconds"] for s in p["shots"]),min(60,15*count))
        for text in (self.shot_table(0),self.shot_table(16),self.shot_table().replace("| 2 |","| 3 |"),
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

    def test_profile_generation_and_revision_scope(self):
        p=wf.new()
        p["story"]="橘白色小猫戴着蓝色项圈，在黄昏的花店门口等待。"
        wf.install_plan(p,plan())
        for s in p["shots"]:
            for field in ("scene","cast"):
                wf.review(p,s+"."+field,True,"")
        target=wf.character("小猫")+".profile"
        description=character_profiles.description("小猫",cat_profile())
        models={"text":{"model":"test"}}
        with patch.object(providers,"text",return_value=cat_profile()) as text:
            wf.execute(p,target,"",models)
            instruction=text.call_args.args[1]
            initial=json.loads(text.call_args.args[2])
            self.assertEqual(initial,{"name":"小猫","species":"猫","other_characters":[],"story":p["story"],"note":"",
                                      "storyboard":[dict(shot=s,action=wf.visual(p,s)["plot"]) for s in p["shots"]]})
            self.assertIn("只为 name 指定的单个角色",instruction)
            self.assertIn("角色外观范围",instruction)
            self.assertIn("场景地点、时间、光线、背景、剧情",instruction)
            wf.execute(p,target,"项圈改成红色",models)
            revision=json.loads(text.call_args.args[2])
            self.assertEqual(revision["previous"],description)
            self.assertEqual(revision["note"],"项圈改成红色")
            self.assertNotIn("scenes",revision)
            self.assertEqual(text.call_args.args[1],instruction)
        self.assertEqual(wf.value(p,target),description)
        self.assertEqual(len(p["nodes"][target]["versions"]),2)

    def test_profile_character_identity_isolation(self):
        p=wf.new()
        p["story"]="小禾是黑色短发女孩，穿黄色外套。爷爷留白胡须，穿蓝色长衫。"
        rows=plan()
        for row in rows["shots"]:
            row["cast"]=["小禾","爷爷"]
        wf.install_plan(p,rows)
        for s in p["shots"]:
            for field in ("scene","cast"):
                wf.review(p,s+"."+field,True,"")
        targets={n:wf.character(n)+".profile" for n in ("小禾","爷爷")}
        descriptions={"小禾":"黑色短发女孩，穿黄色外套。","爷爷":"白胡须老人，穿蓝色长衫。"}
        def describe(cfg,instruction,material):
            payload=json.loads(material)
            name=payload["name"]
            self.assertEqual(payload["other_characters"],[n for n in targets if n!=name])
            self.assertNotIn("scenes",payload)
            return {"name":name,"description":descriptions[name]}
        with patch.object(providers,"text",side_effect=describe):
            for target in targets.values():
                wf.execute(p,target,"",{"text":{"model":"test"}})
        for name,target in targets.items():
            self.assertEqual(wf.value(p,target),descriptions[name])
        wrong={"name":"爷爷","description":descriptions["爷爷"]}
        with patch.object(providers,"text",return_value=wrong) as text:
            self.assertRaisesRegex(ValueError,"姓名与当前角色",wf.execute,p,targets["小禾"],"",{"text":{"model":"test"}})
            self.assertEqual(text.call_count,2)
        self.assertEqual(len(p["nodes"][targets["小禾"]]["versions"]),1)

    def test_profile_draft_uses_only_target_appearance_shots(self):
        p=wf.new()
        p["story"]="小禾走进花店，爷爷留在家里。"
        rows=plan()
        for i,row in enumerate(rows["shots"]):
            row["cast"]=["小禾"] if i<2 else ["爷爷"]
        wf.install_plan(p,rows)
        for s in p["shots"]:
            for field in ("scene","cast"):
                wf.review(p,s+"."+field,True,"")
        with patch.object(providers,"text",return_value={"name":"小禾","description":"黑色短发女孩，穿黄色外套。"}) as text:
            wf.execute(p,wf.character("小禾")+".profile","",{"text":{"model":"test"}})
        payload=json.loads(text.call_args.args[2])
        self.assertEqual([row["shot"] for row in payload["storyboard"]],["S01","S02"])
        self.assertTrue(all(set(row)=={"shot","action"} for row in payload["storyboard"]))
        self.assertIn("storyboard",text.call_args.args[1])

    def test_environment_draft_uses_current_shot_and_keeps_history(self):
        p=wf.new()
        wf.install_plan(p,plan())
        wf.review(p,"S02.scene",True,"")
        description="黄昏的花店，木质花架沿墙排列，暖色灯光照亮入口的瓷砖地面。"
        with patch.object(providers,"text",return_value={"description":description}) as text:
            wf.execute(p,"S02.environment","",{"text":{"model":"test"}})
            payload=json.loads(text.call_args.args[2])
            self.assertEqual(payload,{"scenes":[wf.visual(p,"S02")],"note":""})
            self.assertIn("纯环境空镜",text.call_args.args[1])
            self.assertEqual(wf.status(p,"S02.environment"),"待审核")
            self.assertNotIn("S01.environment",p["nodes"])
            wf.execute(p,"S02.environment","改成冷色灯光",{"text":{"model":"test"}})
            revised=json.loads(text.call_args.args[2])
            self.assertEqual(revised["previous"],description)
            self.assertEqual(revised["note"],"改成冷色灯光")
        self.assertEqual(len(p["nodes"]["S02.environment"]["versions"]),2)

    def test_shared_scene_groups_reuse_one_plate_for_multiple_shots(self):
        p=wf.new()
        rows=plan()
        rows["shots"][2]["scene"]["time"]="夜晚"
        rows["shots"][3]["scene"]["place"]="教室"
        wf.install_plan(p,rows)
        for s in p["shots"]:
            for field in wf.FIELDS:
                wf.review(p,s+"."+field,True,"")
        char=wf.character("小猫")
        wf.version(p,char+".profile","橘白色小猫")
        wf.review(p,char+".profile",True,"")
        wf.version(p,char+".sheet","c"*64+".png")
        wf.review(p,char+".sheet",True,"")
        wf.version(p,"S02.environment","黄昏的无人花店")
        wf.review(p,"S02.environment",True,"")
        wf.version(p,"S02.plate","a"*64+".png")
        wf.review(p,"S02.plate",True,"")
        before=copy.deepcopy(p["nodes"]["S02.plate"])
        wf.group_scenes(p)
        self.assertEqual(wf.view(p)["scene_groups"],{"S02":["S01","S02"],"S03":["S03"],"S04":["S04"]})
        self.assertEqual(p["nodes"]["S02.plate"],before)
        with patch.object(providers,"image",return_value="b"*64+".png") as image:
            for s in ("S01","S02"):
                wf.execute(p,s+".first","",{"text":{"model":"test"},"image":{"model":"test"}})
                wf.review(p,s+".first",True,"")
        for call in image.call_args_list:
            self.assertEqual(call.args[2][0],"a"*64+".png")
        self.assertNotIn("S01.plate",p["nodes"])
        self.assertIn("S02.plate",p["nodes"]["S01.first"]["versions"][-1]["inputs"])
        wf.version(p,"S02.environment","花架布局已修改")
        for s in ("S01","S02"):
            self.assertEqual(wf.status(p,s+".first"),"上游已变更")
        wf.share_scene(p,"S01","S01")
        self.assertEqual(wf.scene_owner(p,"S01"),"S01")
        self.assertEqual(wf.scene_owner(p,"S02"),"S02")
        self.assertRaisesRegex(ValueError,"地点和时间段",wf.share_scene,p,"S01","S03")
        self.assertRaisesRegex(ValueError,"地点和时间段",wf.share_scene,p,"S01","S04")
        st.validate(p)

    def test_scene_source_http_routing_and_package_roundtrip(self):
        p=wf.new()
        wf.install_plan(p,plan())
        wf.review(p,"S01.scene",True,"")
        with st.db() as c:
            st.save(c,p,fresh=True)
        state=app.action(dict(op="scene-source",id=p["id"],revision=p["revision"],target="S02",source="S01"))
        state=app.action(dict(op="edit",id=p["id"],revision=state["project"]["revision"],target="S02.environment",value="共享无人环境"))
        out=state["project"]
        self.assertIn("S01.environment",out["nodes"])
        self.assertNotIn("S02.environment",out["nodes"])
        self.assertEqual(out["scene_owners"]["S02"],"S01")
        archive=BytesIO()
        st.pack(out,archive)
        imported=st.unpack(archive)
        self.assertEqual(wf.scene_owner(imported,"S02"),"S01")
        changed=dict(wf.value(imported,"S02.scene"),place="另一个街区")
        wf.version(imported,"S02.scene",changed)
        self.assertEqual(wf.scene_owner(imported,"S02"),"S02")
        out["scene_sources"]={"S01":"S02","S02":"S01"}
        self.assertRaisesRegex(ValueError,"循环",st.validate,out)

    def test_style_legacy_media_invalidation_and_restore(self):
        p=wf.new()
        wf.install_plan(p,plan())
        for s in p["shots"]:
            for field in wf.FIELDS:
                wf.review(p,s+"."+field,True,"")
        char=wf.character("小猫")
        for key in [char+".profile",char+".sheet"]+[s+"."+kind for s in p["shots"]
                   for kind in ("environment","plate","first","last","video")]+["final.film"]:
            kind=key.split(".")[1]
            val="外观与环境" if kind in ("profile","environment") else "a"*64+(".mp4" if kind in ("film","video") else ".png")
            wf.version(p,key,val)
            wf.review(p,key,True,"")
        # Documents created before style support retain the original illustration default.
        del p["style"]
        for node in p["nodes"].values():
            for v in node["versions"]:
                v.pop("style",None)
        self.assertEqual(wf.status(p,"final.film"),"已确认")
        self.assertEqual(wf.view(p)["style"],wf.DEFAULT_STYLE)
        p["style"]="水彩绘本，柔和配色"
        self.assertEqual(wf.status(p,char+".profile"),"已确认")
        self.assertEqual(wf.status(p,"S01.environment"),"已确认")
        for key in (char+".sheet","S01.plate","S01.first","S01.last","S01.video","final.film"):
            self.assertEqual(wf.status(p,key),"上游已变更")
        self.assertRaises(ValueError,wf.review,p,char+".sheet",True,"")
        with st.db() as c:
            st.save(c,p,fresh=True)
        restored=app.action(dict(op="restore",id=p["id"],revision=p["revision"],target=char+".sheet",index=0))["project"]
        self.assertEqual(wf.status(restored,char+".sheet"),"上游已变更")
        self.assertEqual(restored["nodes"][char+".sheet"]["versions"][-1]["restored_from"],1)
        self.assertRaises(ValueError,wf.review,restored,char+".sheet",True,"")
        wf.version(p,char+".sheet","b"*64+".png")
        self.assertEqual(p["nodes"][char+".sheet"]["versions"][-1]["style"],p["style"])

    def test_style_save_and_project_package(self):
        p=app.action({"op":"new"})["project"]
        style="赛璐璐动画，清晰线条，低饱和暖色调"
        out=app.action(dict(op="save",id=p["id"],revision=p["revision"],title=p["title"],story="小猫",style=style))
        self.assertEqual(out["project"]["style"],style)
        code,data=self.http("GET","/package?id="+p["id"])
        self.assertEqual(code,200)
        imported=st.unpack(BytesIO(data))
        self.assertEqual(imported["style"],style)
        del imported["style"]
        st.validate(imported)
        self.assertEqual(wf.view(imported)["style"],wf.DEFAULT_STYLE)

    def test_scene_rollback_reuses_matching_approved_environment(self):
        p=wf.new()
        wf.install_plan(p,plan())
        wf.review(p,"S01.scene",True,"")
        wf.version(p,"S01.environment","黄昏的无人花店")
        wf.review(p,"S01.environment",True,"")
        wf.version(p,"S01.plate","a"*64+".png")
        wf.review(p,"S01.plate",True,"")
        original=copy.deepcopy(p["nodes"]["S01.scene"]["versions"][0])
        changed=dict(original["value"],plot="少女手持画夹向前走")
        wf.version(p,"S01.scene",changed)
        wf.review(p,"S01.scene",True,"")
        self.assertEqual(wf.status(p,"S01.plate"),"上游已变更")
        wf.restore(p,"S01.scene",0,"还原剧情")
        self.assertEqual(wf.value(p,"S01.scene"),original["value"])
        self.assertEqual(wf.status(p,"S01.scene"),"待审核")
        self.assertEqual(wf.status(p,"S01.plate"),"上游已变更")
        wf.review(p,"S01.scene",True,"")
        self.assertEqual(wf.status(p,"S01.environment"),"已确认")
        self.assertEqual(wf.status(p,"S01.plate"),"已确认")
        self.assertEqual(p["nodes"]["S01.scene"]["versions"][0],original)
        self.assertEqual(p["nodes"]["S01.plate"]["versions"][0]["inputs"],{"S01.environment":1})
        st.validate(p)

    def test_http_rollback_keeps_media_history_without_model_calls(self):
        p=wf.new()
        wf.install_plan(p,plan())
        wf.review(p,"S01.scene",True,"")
        wf.version(p,"S01.environment","无人花店")
        wf.review(p,"S01.environment",True,"")
        for prefix in ("a","b"):
            wf.version(p,"S01.plate",prefix*64+".png")
            wf.review(p,"S01.plate",True,"")
        old=copy.deepcopy(p["nodes"]["S01.plate"]["versions"])
        with st.db() as c:
            st.save(c,p,fresh=True)
        with patch.object(providers,"request") as model:
            code,data=self.http("POST","/action",dict(op="restore",id=p["id"],revision=p["revision"],target="S01.plate",index=0))
        model.assert_not_called()
        self.assertEqual(code,200)
        restored=json.loads(data)["project"]
        versions=restored["nodes"]["S01.plate"]["versions"]
        self.assertEqual(versions[:2],old)
        self.assertEqual(versions[-1]["value"],"a"*64+".png")
        self.assertEqual(versions[-1]["restored_from"],1)
        self.assertEqual(wf.status(restored,"S01.plate"),"待审核")
        self.assertEqual(versions[-1]["reviews"],[])
        wf.review(restored,"S01.plate",True,"")
        self.assertEqual(wf.status(restored,"S01.plate"),"已确认")
        st.validate(restored)

    def test_restored_media_retains_its_actual_upstream_dependencies(self):
        p=wf.new()
        wf.install_plan(p,plan())
        wf.review(p,"S01.scene",True,"")
        wf.version(p,"S01.environment","原来的花店")
        wf.review(p,"S01.environment",True,"")
        wf.version(p,"S01.plate","a"*64+".png")
        wf.version(p,"S01.environment","全新的教室")
        wf.review(p,"S01.environment",True,"")
        wf.restore(p,"S01.plate",0)
        self.assertEqual(p["nodes"]["S01.plate"]["versions"][-1]["inputs"],{"S01.environment":1})
        self.assertEqual(wf.status(p,"S01.plate"),"上游已变更")
        self.assertRaises(ValueError,wf.review,p,"S01.plate",True,"")
        for index in (-1,5,True,"0"):
            self.assertRaises(ValueError,wf.restore,p,"S01.plate",index)
        wf.restore(p,"S01.environment",0)
        wf.review(p,"S01.environment",True,"")
        self.assertEqual(wf.status(p,"S01.plate"),"待审核")

    def test_muse_image_ratios_references_and_async_download(self):
        source=st.DATA/"source.webp"
        providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=green:s=90x160","-frames:v",1,source])
        raw=source.read_bytes()
        names=["a"*64+".webp","b"*64+".webp"]
        for name in names:
            st.asset(name).write_bytes(raw)
        for wide,refs,ratio in [(True,[],"3:2"),(False,[],"9:16"),(False,names,"9:16")]:
            tasks=[]
            cfg=dict(mode="api",model="muse-image-ext",url="https://example.test/v1",key="test")
            responses=[{"data":[{"task_id":"muse-123"}]},{"data":{"status":"processing"}},
                       {"data":{"status":"completed","result":{"images":[{"url":["https://cdn.example/image.webp"]}]}}},raw]
            with patch.object(providers,"request",side_effect=responses) as req,patch.object(providers.time,"sleep"):
                result=providers.image(cfg,"水彩绘本",refs,wide=wide,record_task=tasks.append)
            submitted=req.call_args_list[0]
            self.assertEqual(submitted.args[0],"https://example.test/v1/images/generations")
            body=submitted.args[1]
            self.assertEqual(body["aspect_ratio"],ratio)
            self.assertEqual(body["n"],1)
            self.assertNotIn("size",body)
            self.assertNotIn("resolution",body)
            self.assertEqual(body.get("image_urls",[]),[providers.data_url(n) for n in refs])
            self.assertRegex(submitted.kwargs["extra_headers"]["Idempotency-Key"],r"^[a-f0-9]{32}$")
            self.assertEqual(req.call_args_list[1].args[0],"https://example.test/v1/tasks/muse-123")
            self.assertEqual(tasks,["muse-123"])
            self.assertTrue(st.asset(result).read_bytes().startswith(b"\x89PNG"))

    def test_muse_failure_keeps_task_and_does_not_resubmit(self):
        cfg=dict(mode="api",model="muse-image-ext",url="https://example.test")
        tasks=[]
        with patch.object(providers,"request",side_effect=[{"data":[{"task_id":"muse-fail"}]},
                          {"data":{"status":"failed","error":{"message":"fixture failure"}}}]) as req:
            self.assertRaisesRegex(ValueError,"muse-fail",providers.image,cfg,"test",[],record_task=tasks.append)
        self.assertEqual(tasks,["muse-fail"])
        self.assertEqual(req.call_count,2)
        self.assertEqual(req.call_args_list[0].args[0],"https://example.test/v1/images/generations")
        with patch.object(providers,"request") as req:
            self.assertRaisesRegex(ValueError,"9 张参考图",providers.muse_image,cfg,"test",["a"]*10,False,tasks.append)
        req.assert_not_called()

    def test_openai_image_size_and_edit_protocol_remain_supported(self):
        source=st.DATA/"source.png"
        providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=blue:s=90x160","-frames:v",1,source])
        raw=source.read_bytes()
        name="a"*64+".png"
        st.asset(name).write_bytes(raw)
        cfg=dict(mode="api",model="gpt-image-1",url="https://example.test/v1")
        for refs,wide in [([],True),([name],False)]:
            with patch.object(providers,"request",side_effect=[{"data":[{"url":"https://cdn.example/image.png"}]},raw]) as req:
                result=providers.image(cfg,"test",refs,wide=wide)
            self.assertTrue(st.asset(result).exists())
            first=req.call_args_list[0]
            if refs:
                self.assertTrue(first.args[0].endswith("/images/edits"))
                self.assertIn(b'name="size"',first.args[1])
                self.assertIn(b"1024x1536",first.args[1])
                self.assertIn(b'name="image[]"',first.args[1])
            else:
                self.assertEqual(first.args[1]["size"],"1536x1024")
                self.assertNotIn("aspect_ratio",first.args[1])

    def test_calico_rejects_human_features_and_incorrect_coat(self):
        valid=cat_profile("三花猫")
        self.assertIn("黑、白、橘",character_profiles.description("三花猫",valid))
        for field,text in [("体型","158cm，肩窄腰细，少女感比例"),
                           ("头部特征","鹅蛋脸，黑色长直发和齐刘海"),
                           ("服饰与配饰","百褶裙，手腕红绳，帆布画夹"),
                           ("毛色与花纹","深褐色虎斑纹，腹部纯白"),
                           ("头部特征","黄昏逆光，路灯暖黄光")]:
            bad=copy.deepcopy(valid)
            bad["attributes"][field]=text
            self.assertRaises(ValueError,character_profiles.description,"三花猫",bad)
        bad=copy.deepcopy(valid)
        bad["description"]="少女和小猫的混合外观"
        self.assertRaises(ValueError,character_profiles.description,"三花猫",bad)

    def test_animal_profile_retries_and_discards_polluted_context(self):
        p=wf.new()
        p["story"]="黑色长发少女喂了一只三花猫，小猫身上有黑白橘三色斑块。"
        rows=plan()
        for row in rows["shots"]:
            row["cast"]=["少女","三花猫"]
        wf.install_plan(p,rows)
        for s in p["shots"]:
            for field in ("scene","cast"):
                wf.review(p,s+"."+field,True,"")
        key=wf.character("三花猫")+".profile"
        polluted="158cm的少女感体型，鹅蛋脸，黑色长直发，百褶裙。物种：三花猫（Tabby）。"
        wf.version(p,key,polluted)
        wrong=cat_profile("三花猫")
        wrong["attributes"]["头部特征"]="少女的黑色长直发"
        with patch.object(providers,"text",side_effect=[wrong,cat_profile("三花猫")]) as model:
            wf.execute(p,key,"",{"text":{"model":"test"}})
        self.assertEqual(model.call_count,2)
        self.assertNotIn("previous",json.loads(model.call_args_list[0].args[2]))
        self.assertIn("未通过校验",model.call_args.args[1])
        self.assertNotIn("少女",wf.value(p,key))
        self.assertEqual(len(p["nodes"][key]["versions"]),2)
        before=copy.deepcopy(p)
        with patch.object(providers,"text",return_value=wrong):
            self.assertRaisesRegex(ValueError,"混入无关特征",wf.execute,p,key,"",{"text":{"model":"test"}})
        self.assertEqual(p,before)

    def test_minimax_and_local_video_reference_only_first_frame(self):
        keys=["a"*64+".png"]
        for k in keys:
            st.asset(k).write_bytes(k.encode())
        mp4=b"\x00\x00\x00\x18ftypisom"+b"test"
        task=[]
        for suffix in ("", "/", "/v1", "/v1/", "/v2", "/v2/"):
            task.clear()
            with self.subTest(suffix=suffix), patch.object(providers,"request",side_effect=[{"task_id":"m1"},{"task":{"status":"succeeded","content":{"url":"https://cdn.example/clip"}}},mp4]) as req:
                result=providers.video(dict(mode="api",model="MiniMax-H3",url="https://example.test"+suffix),"剧情",keys,12,task.append)
                body=req.call_args_list[0].args[1]
                self.assertEqual(req.call_args_list[0].args[0],"https://example.test/v2/video_generation")
                self.assertEqual(req.call_args_list[1].args[0],"https://example.test/v2/query/video_generation/m1")
                self.assertEqual([x["role"] for x in body["content"][1:]],["first_frame"])
                self.assertEqual((task,body["ratio"],body["duration"]),(["m1"],"9:16",12))
                self.assertTrue(st.asset(result).exists())
        def fake_run(cmd,cwd):
            self.assertEqual([cmd[i+1] for i,x in enumerate(cmd) if x=="--anchor"],["first"])
            self.assertEqual([cmd[i+1] for i,x in enumerate(cmd) if x=="--image"],list(map(st.asset,keys)))
            Path(cmd[-1]).write_bytes(mp4)
        with patch.object(providers,"run",side_effect=fake_run):
            providers.video({"mode":"local"},"剧情",keys,12,task.append)

    def test_first_frame_workflow_needs_no_tail_and_preserves_legacy_history(self):
        p=wf.new()
        wf.install_plan(p,plan())
        for s in p["shots"]:
            for field in wf.FIELDS:
                wf.review(p,s+"."+field,True,"")
        char=wf.character("小猫")
        src=st.DATA/"fixture.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        first=st.persist(src,"png")
        src.write_bytes(b"\x89PNG\r\n\x1a\ntail")
        tail=st.persist(src,"png")
        clip=st.DATA/"fixture.mp4"
        clip.write_bytes(b"\x00\x00\x00\x18ftypisomtest")
        video=st.persist(clip,"mp4")
        for key in (char+".profile",char+".sheet","S01.environment","S01.plate","S01.first"):
            wf.version(p,key,"描述" if key.endswith((".profile",".environment")) else first)
            if key != "S01.first":
                wf.review(p,key,True,"")
        self.assertRaisesRegex(ValueError,"分镜1的首帧",wf.ready,p,"S01.video")
        wf.review(p,"S01.first",True,"")
        self.assertNotIn("S01.last",p["nodes"])
        with patch.object(providers,"video",return_value=video) as model:
            wf.execute(p,"S01.video","",{"text":{},"video":{"model":"test"}})
        self.assertEqual(model.call_args.args[2],[first])
        self.assertIn("以参考首帧为起始画面",model.call_args.args[1])
        self.assertNotIn("S01.last",p["nodes"]["S01.video"]["versions"][-1]["inputs"])
        wf.review(p,"S01.video",True,"")
        # An unreviewed historical tail is irrelevant to newly generated videos.
        wf.version(p,"S01.last",tail)
        self.assertEqual(wf.status(p,"S01.video"),"已确认")
        wf.ready(p,"S01.video")
        with patch.object(providers,"image") as image:
            self.assertRaisesRegex(ValueError,"仅生成首帧",wf.execute,p,"S01.last","",{"text":{}})
            image.assert_not_called()
        # Old video versions still record and validate their original two-frame inputs.
        wf.review(p,"S01.last",True,"")
        p["nodes"]["S01.video"]["versions"][-1]["inputs"]["S01.last"]=1
        self.assertEqual(wf.status(p,"S01.video"),"已确认")
        st.validate(p)
        bundle=BytesIO()
        st.pack(p,bundle)
        restored=st.unpack(bundle)
        self.assertEqual(restored["nodes"],p["nodes"])
        self.assertEqual(wf.status(restored,"S01.video"),"已确认")
        self.assertTrue(st.asset(tail).exists())
        wf.version(restored,"S01.first",tail)
        self.assertEqual(wf.status(restored,"S01.video"),"上游已变更")

    def test_video_adapters_reject_extra_reference_images(self):
        for mode in ("local","api"):
            for refs in ([],["first","last"]):
                with self.subTest(mode=mode,refs=refs),patch.object(providers,"request") as req,patch.object(providers,"run") as run:
                    self.assertRaisesRegex(ValueError,"只接受一张",providers.video,{"mode":mode},"剧情",refs,12,lambda _:None)
                    req.assert_not_called()
                    run.assert_not_called()

    def test_apimart_video_uploads_only_first_frame_and_polls_its_own_protocol(self):
        keys=["a"*64+".png"]
        for key in keys:
            st.asset(key).write_bytes(key.encode())
        mp4=b"\x00\x00\x00\x18ftypisom"+b"test"
        for model, seconds, field in (("seedance-1-0-pro-fast",12,"aspect_ratio"),
                                      ("seedance-1-0-pro-quality",12,"aspect_ratio"),
                                      ("seedance-2.0",15,"size"),
                                      ("seedance-2.0-fast",15,"size"),
                                      ("seedance-2.0-mini",15,"size")):
            for suffix in ("", "/v1/", "/v2"):
                recorded=[]
                responses=[{"url":"https://cdn.example/first.png"},
                           {"data":[{"task_id":"task/a"}]},{"data":{"status":"processing"}},
                           {"data":{"status":"completed","result":{"videos":[{"url":["https://cdn.example/video.mp4"]}]}}},mp4]
                cfg=dict(mode="api",model=model,url="https://api.apimart.ai"+suffix,key="TEST-ONLY")
                with self.subTest(model=model,suffix=suffix), patch.object(providers,"request",side_effect=responses) as req, patch.object(providers.time,"sleep"):
                    result=providers.video(cfg,"分镜剧情",keys,seconds,recorded.append)
                self.assertEqual(st.asset(result).read_bytes(),mp4)
                self.assertEqual(recorded,["task/a"])
                for i in (0,):
                    args=req.call_args_list[i].args
                    self.assertEqual(args[0],"https://api.apimart.ai/v1/uploads/images?retention_days=3")
                    self.assertIn(b'name="file"',args[1])
                    self.assertIn(keys[i].encode(),args[1])
                    self.assertNotIn(b'image[]',args[1])
                    self.assertEqual(args[2],"TEST-ONLY")
                    self.assertIn("boundary=",args[3])
                self.assertEqual(req.call_args_list[1].args[0],"https://api.apimart.ai/v1/videos/generations")
                body=req.call_args_list[1].args[1]
                self.assertEqual((body["model"],body["duration"],body[field],body["resolution"]),(model,seconds,"9:16","720p"))
                self.assertEqual(body["prompt"],"分镜剧情")
                self.assertEqual(body["image_with_roles"],[{"role":"first_frame","url":"https://cdn.example/first.png"}])
                self.assertNotIn("ratio",body)
                self.assertNotIn("content",body)
                self.assertEqual(req.call_args_list[2].args[0],"https://api.apimart.ai/v1/tasks/task%2Fa")
                self.assertEqual(req.call_args_list[4].kwargs,{"binary":True})

    def test_apimart_video_rejects_incompatible_models_and_duration_before_upload(self):
        for model,seconds,error in (("seedance-1-0-pro-fast",15,"2–12"),
                                    ("seedance-1-0-pro-quality",15,"2–12"),
                                    ("seedance-2.0",16,"4–15"),
                                    ("seedance-2.0",12.5,"整秒"),
                                    ("not-a-supported-model",12,"模型名称")):
            cfg=dict(mode="api",model=model,url="https://api.apimart.ai/v1")
            with self.subTest(model=model,seconds=seconds),patch.object(providers,"request") as req:
                self.assertRaisesRegex(ValueError,error,providers.video,cfg,"剧情",["missing-first"],seconds,lambda _:None)
                req.assert_not_called()
        self.assertEqual(providers.video_api_base("https://proxy.test/api/v1/","v2"),"https://proxy.test/api/v2")
        for url in ("file:///tmp/video","https://example.test/v1?key=secret","https://example.test/#v1"):
            self.assertRaises(ValueError,providers.video_api_base,url,"v2")

    def test_apimart_video_failures_keep_remote_task_and_do_not_resubmit(self):
        cfg=dict(mode="api",model="seedance-1-0-pro-quality",url="https://api.apimart.ai/v1")
        keys=["a"*64+".png"]
        for key in keys:
            st.asset(key).write_bytes(b"fixture")
        for status in ("failed","cancelled","expired","processing"):
            recorded=[]
            result={"data":{"status":status,"error":{"message":"generation failed"}}}
            responses=[{"url":"https://cdn.example/first"},{"data":[{"task_id":"saved-id"}]}]+[result]*450
            with self.subTest(status=status),patch.object(providers,"request",side_effect=responses) as req,patch.object(providers.time,"sleep"):
                self.assertRaisesRegex(ValueError,"saved-id",providers.video,cfg,"剧情",keys,12,recorded.append)
            self.assertEqual(recorded,["saved-id"])
            self.assertEqual(sum(c.args[0].endswith("/videos/generations") for c in req.call_args_list),1)
        with patch.object(providers,"request",return_value={"url":""}) as req:
            self.assertRaisesRegex(ValueError,"上传未返回有效地址",providers.video,cfg,"剧情",keys,12,lambda _:None)
            self.assertEqual(req.call_count,1)

    def test_manual_video_import_and_review_without_first_frame(self):
        p=wf.new()
        wf.install_plan(p,plan())
        with st.db() as c:
            st.save(c,p,fresh=True)
        url=f"/shot-video?id={p['id']}&revision={p['revision']}&target=S01.video"
        self.assertEqual(self.http("POST",url,b"invalid","video/mp4")[0],400)
        clip=st.DATA/"sample.mp4"
        providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=s=90x160","-t","1",clip])
        raw=clip.read_bytes()
        code,body=self.http("POST",url,raw,"video/mp4")
        self.assertEqual(code,200,body)
        project=json.loads(body)["project"]
        self.assertEqual(wf.status(project,"S01.video"),"待审核")
        self.assertEqual(st.asset(wf.value(project,"S01.video")).read_bytes(),raw)
        wf.review(project,"S01.video",True,"")
        self.assertEqual(wf.status(project,"S01.video"),"已确认")
        self.assertEqual(self.http("POST",url,raw,"video/mp4")[0],400)

    def test_full_production_export_and_archive(self):
        p=wf.new("测试短片")
        p["style"]="水彩绘本，柔和低饱和配色"
        p["story"]="小猫在花店等到了主人。"
        wf.install_plan(p,plan())
        models={k:{"model":"test double"} for k in ("text","image","video")}
        char=wf.character("小猫")
        for s in p["shots"]:
            for f in wf.FIELDS:
                wf.review(p,s+"."+f,True,"")
        def make_image(cfg,prompt,refs,wide=False,record_task=lambda _:None):
            self.assertIn(p["style"],prompt)
            if "第一张图" in prompt:
                self.assertEqual(len(refs),2)
                self.assertNotIn("花店要关门了",prompt)
            img=st.DATA/"fixture.png"
            providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=green:s=90x160","-frames:v",1,img])
            return st.persist(img,"png")
        def make_video(cfg,prompt,refs,seconds,remote):
            self.assertIn(p["style"],prompt)
            self.assertEqual(len(refs),1)
            src=st.DATA/"clip.mp4"
            providers.run([providers.FF,"-v","error","-f","lavfi","-i","color=green:s=90x160:r=24",
                           "-f","lavfi","-i","sine=frequency=330","-t",seconds,"-c:v","libx264","-c:a","aac",src])
            return st.persist(src,"mp4")
        order=[char+".profile",char+".sheet"]+[s+"."+k for s in p["shots"] for k in ("environment","plate","first","video")]
        def text_result(cfg,instruction,material):
            return cat_profile() if json.loads(material).get("name")=="小猫" else {"description":"环境描述"}
        with patch.object(providers,"text",side_effect=text_result),patch.object(providers,"image",side_effect=make_image),patch.object(providers,"video",side_effect=make_video):
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
