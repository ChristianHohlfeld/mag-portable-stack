"""Contract tests for the native (C++) MAG model-routing gateway.
All HTTP servers are process-local fixtures. No model binaries or privileges needed."""
import grp, http.client, http.server, json, os, pathlib, socket, subprocess
import tempfile, threading, time, unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class FakeModel(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ready = True
    calls = []
    def log_message(self, *_): pass
    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health": self.reply(200 if type(self).ready else 503, {"status":"ok" if type(self).ready else "loading"})
        else: self.reply(200, {"ok":True})
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"))
        body=self.rfile.read(n)
        try: data=json.loads(body)
        except ValueError: data={}
        type(self).calls.append((self.path,data))
        if self.path == "/v1/chat/completions":
            if any(x.get("content") == "stream" for x in data.get("messages",[]) if isinstance(x,dict)):
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream")
                self.send_header("Transfer-Encoding","chunked")
                self.end_headers()
                for delta in ({"choices":[{"index":0,"delta":{"content":"Hallo ","reasoning_content":"denken "}}]},
                              {"choices":[{"index":0,"delta":{"content":"Welt","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_","arguments":"{\"a\":"}}]}}]},
                              {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"command","arguments":"1}"}}]},"finish_reason":"tool_calls"}]}):
                    payload=("data: "+json.dumps(delta)+"\n\n").encode()
                    self.wfile.write(("%X\r\n"%len(payload)).encode()+payload+b"\r\n")
                self.wfile.write(b"0\r\n\r\n");self.wfile.flush()
                return
            self.reply(200, {"choices":[{"message":{"role":"assistant","content":"antwort"},"finish_reason":"stop"}],"model":data.get("model")})
        else: self.reply(200,{"ok":True})

class Q4(FakeModel): ready=True;calls=[]
class IQ3(FakeModel): ready=True;calls=[]

def request(portno,method="GET",path="/health",body=None):
    c=http.client.HTTPConnection("127.0.0.1",portno,timeout=5)
    data=json.dumps(body) if body is not None else None
    c.request(method,path,body=data,headers={"Content-Type":"application/json"})
    r=c.getresponse(); raw=r.read();status=r.status;c.close()
    try: return status,json.loads(raw)
    except ValueError:return status,raw

def control(path,method="GET",body=None):
    payload=json.dumps(body).encode() if body else b""
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(3);s.connect(str(path))
    s.sendall((method+" /models HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: "+str(len(payload))+"\r\n\r\n").encode()+payload)
    raw=b""
    while True:
        x=s.recv(65536)
        if not x:break
        raw+=x
    s.close()
    status=int(raw.split(b"\r\n",1)[0].split()[1])
    return status,json.loads(raw.split(b"\r\n\r\n",1)[1])

class NativeGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="mag-switch-")
        cls.dir=pathlib.Path(cls.temp.name)
        cls.path=cls.dir/"control.sock"
        cls.supervisor_path=cls.dir/"supervisor.sock"
        cls.fail_path=cls.dir/"fail-model"
        cls.state=cls.dir/"model.json"
        cls.q4p,cls.iq3p,cls.gwp=port(),port(),port()
        cls.q4=http.server.ThreadingHTTPServer(("127.0.0.1",cls.q4p),Q4)
        cls.iq3=http.server.ThreadingHTTPServer(("127.0.0.1",cls.iq3p),IQ3)
        for server in (cls.q4,cls.iq3):threading.Thread(target=server.serve_forever,daemon=True).start()
        cls.env=dict(os.environ,MAG_AUDIT_LISTEN_PORT=str(cls.gwp),MAG_MODEL_Q4_PORT=str(cls.q4p),
            MAG_MODEL_IQ3_PORT=str(cls.iq3p),MAG_MODEL_CONTROL_SOCKET=str(cls.path),
            MAG_MODEL_CONTROL_GROUP=grp.getgrgid(os.getgid()).gr_name,
            MAG_MODEL_STATE_FILE=str(cls.state),MAG_SUPERVISOR_TEST="1",
            MAG_MODEL_SUPERVISOR_SOCKET=str(cls.supervisor_path),
            MAG_SUPERVISOR_TEST_FAIL_FILE=str(cls.fail_path),
            MAG_SUPERVISOR_GROUP=grp.getgrgid(os.getgid()).gr_name,
            MAG_AUDIT_LOG_DIR=str(cls.dir/"logs"),
            MAG_AUDIT_SECRETS_FILE=str(cls.dir/"missing-secrets"),
            MAG_MODEL_HEALTH_INTERVAL="1",MAG_MODEL_SWITCH_TIMEOUT="2")
        cls.sup=subprocess.Popen([str(ROOT/"build/mag-model-supervisor")],env=cls.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.sup.poll() is not None:raise RuntimeError(cls.sup.stderr.read().decode())
            if cls.supervisor_path.exists():
                try:
                    if control(cls.supervisor_path)[0]==200:break
                except OSError:pass
            time.sleep(.05)
        else:raise RuntimeError("Supervisor not ready")
        cls.start()
    @classmethod
    def start(cls):
        cls.proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=cls.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.proc.poll() is not None:raise RuntimeError(cls.proc.stderr.read().decode())
            if cls.path.exists():
                try:
                    code,_=request(cls.gwp)
                    if code==200:return
                except (ConnectionError,OSError): pass
            time.sleep(.05)
        raise RuntimeError("Gateway not ready")
    @classmethod
    def stop(cls):
        cls.proc.terminate()
        try:cls.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:cls.proc.kill();cls.proc.wait()
        cls.proc.stderr.close()
        if cls.path.exists():cls.path.unlink()
    @classmethod
    def tearDownClass(cls):
        cls.stop()
        cls.sup.terminate();cls.sup.wait(timeout=3);cls.sup.stderr.close()
        for server in (cls.q4,cls.iq3):server.shutdown();server.server_close()
        cls.temp.cleanup()
    def until(self,fn,timeout=6):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=fn()
            if result:return result
            time.sleep(.1)
        self.fail("model state never converged")
    def switch(self,target):
        # A just-completed HTTP reply may still have an active proxy lease
        # until the upstream socket closes. A transient 409 is correct: retry
        # briefly, but never tolerate persistent 409 or any 503/supervisor error.
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            code,data=control(self.path,"PUT",{"id":target})
            if code==202:return code,data
            if code!=409:self.fail(f"model switch {target}: HTTP {code}, body {data}")
            time.sleep(.05)
        self.fail(f"model switch {target} remained busy beyond 4 seconds")
    def test_01_initial_q4_and_private_control(self):
        code,data=control(self.path)
        self.assertEqual(code,200);self.assertEqual(data["active"],"q4")
        self.assertEqual(data["pending"],"")
        self.assertEqual(self.path.stat().st_mode & 0o777,0o660)
        self.assertEqual(request(self.gwp,"POST","/v1/chat/completions",{"model":"deepseek-v4-flash-0731"})[0],200)
        self.assertEqual(Q4.calls[-1][1]["model"],"deepseek-v4-flash-0731")
    def test_02_switch_and_persist_without_chat_reset(self):
        code,data=self.switch("iq3")
        self.assertEqual(code,202);self.assertEqual(data["pending"],"iq3")
        self.until(lambda: control(self.path)[1]["active"]=="iq3")
        self.assertEqual(request(self.gwp,"POST","/v1/chat/completions",{"model":"deepseek-v4-flash-0731","messages":[]})[0],200)
        self.assertEqual(IQ3.calls[-1][1]["model"],"deepseek-v4-flash-0731-iq3")
        self.assertEqual(json.loads(self.state.read_text())["active"],"iq3")
        # Restart only the unprivileged audit gateway; selector stays in the
        # privileged model supervisor and is durable on disk.
        self.stop();self.start()
        self.assertEqual(control(self.path)[1]["active"],"iq3")
        self.assertEqual(control(self.supervisor_path)[1]["active"],"iq3")
        self.assertFalse(control(self.supervisor_path)[1]["overlap_detected"])
        # Switching does not mutate any room records or ask the UI to navigate.
    def test_03_bad_model_keeps_previous_healthy(self):
        IQ3.ready=False
        code,data=self.switch("q4")
        self.assertEqual(code,202)
        self.until(lambda:control(self.path)[1]["active"]=="q4")
        code,data=self.switch("iq3")
        self.assertEqual(code,202)
        self.until(lambda:control(self.path)[1]["pending"]=="",timeout=7)
        self.assertEqual(control(self.path)[1]["active"],"q4")
        self.assertTrue(control(self.path)[1]["error"])
        self.assertEqual(request(self.gwp,"POST","/v1/chat/completions",{"model":"x"})[0],200)
        self.assertEqual(Q4.calls[-1][1]["model"],"deepseek-v4-flash-0731")
        self.assertFalse(control(self.supervisor_path)[1]["overlap_detected"])
        IQ3.ready=True
    def test_03b_bidirectional_q4_iq3_q4_never_overlaps(self):
        self.assertEqual(control(self.path)[1]["active"],"q4")
        for target in ("iq3","q4"):
            code,result=self.switch(target)
            self.assertEqual(code,202)
            self.until(lambda:control(self.path)[1]["active"]==target,timeout=8)
            self.assertEqual(control(self.path)[1]["pending"],"")
            live=control(self.supervisor_path)[1]
            self.assertFalse(live["overlap_detected"])
            self.assertEqual(live["resident_q4"],target=="q4")
            self.assertEqual(live["resident_iq3"],target=="iq3")
        self.assertEqual(json.loads(self.state.read_text())["active"],"q4")
    def test_03c_loading_keeps_session_and_queues_inference(self):
        IQ3.ready=False
        code,data=self.switch("iq3")
        self.assertEqual(code,202)
        self.assertEqual(request(self.gwp,"GET","/health")[0],503)
        self.assertEqual(control(self.path)[1]["pending"],"iq3")
        # Only inference is gated; room control remains usable.
        status,_=request(self.gwp,"POST","/v1/chat/completions",{"model":"x","messages":[]})
        self.assertEqual(status,200,"waiting call should resume on healthy Q4 after failback")
        self.assertEqual(control(self.path)[1]["active"],"q4")
        self.assertFalse(control(self.supervisor_path)[1]["overlap_detected"])
        IQ3.ready=True
    def test_03d_supervisor_restart_restores_persistent_selection(self):
        # Simulates a host service restart with selector.json preserved.
        self.assertEqual(control(self.path)[1]["active"],"q4")
        self.sup.terminate()
        self.sup.wait(timeout=5)
        self.sup.stderr.close()
        self.supervisor_path.unlink(missing_ok=True)
        self.sup=subprocess.Popen([str(ROOT/"build/mag-model-supervisor")],env=self.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if self.sup.poll() is not None:
                self.fail("supervisor restart failed: "+self.sup.stderr.read().decode())
            if self.supervisor_path.exists():
                try:
                    if control(self.supervisor_path)[1]["active"]=="q4":
                        break
                except OSError:
                    pass
            time.sleep(.05)
        else:
            self.fail("supervisor not ready after restart")
        self.assertEqual(json.loads(self.state.read_text())["active"],"q4")
        self.assertEqual(request(self.gwp,"POST","/v1/chat/completions",{"model":"x"})[0],200)
    def test_04_only_whitelisted_models_and_control_not_exposed(self):
        self.assertEqual(control(self.path,"PUT",{"id":"../../etc/passwd"})[0],400)
        self.assertNotEqual(request(self.gwp,"PUT","/models",{"id":"iq3"})[0],202)
        self.assertEqual(control(self.path)[1]["active"],"q4")
    def test_06_stream_assembly_and_audit_integrity(self):
        import sys
        status,data=request(self.gwp,"POST","/v1/chat/completions",
            {"model":"x","stream":True,"messages":[{"role":"user","content":"stream"}]})
        self.assertEqual(status,200)
        # HTTP response bytes can reach the client just before the gateway
        # finishes its append-only audit/fsync. Wait for the real audit record,
        # and still fail if it never appears (do not disable audit assertions).
        def recorded_stream():
            recs=[json.loads(line) for f in (self.dir/"logs").rglob("events.jsonl")
                  for line in f.read_text().splitlines()]
            return [rec for rec in recs if rec.get("type")=="llm_response" and rec.get("streamed")]
        entries=self.until(recorded_stream,timeout=8)
        self.assertTrue(entries,"streaming answer must appear in audit")
        msg=entries[-1]["body"]["choices"][0]["message"]
        self.assertEqual(msg["content"],"Hallo Welt")
        self.assertEqual(msg["reasoning_content"],"denken ")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"],"run_command")
        self.assertEqual(msg["tool_calls"][0]["function"]["arguments"],'{"a":1}')
        result=subprocess.run([sys.executable,str(ROOT/"ops/audit/mag-audit-tail"),"--verify"],
            env=dict(os.environ,MAG_AUDIT_LOG_DIR=str(self.dir/"logs")),capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_05_audit_and_request_cancellation_contract(self):
        status,_=request(self.gwp,"POST","/v1/chat/completions",{"model":"x","messages":[{"role":"user","content":"hello"}]})
        self.assertEqual(status,200)
        recs=[json.loads(l) for f in (self.dir/"logs").rglob("events.jsonl") for l in f.read_text().splitlines()]
        requests=[r for r in recs if r.get("type")=="llm_request"]
        responses=[r for r in recs if r.get("type")=="llm_response"]
        self.assertTrue(requests and responses)
        self.assertEqual(requests[-1]["model_variant"],"q4")
        self.assertEqual(requests[-1]["body"]["messages"][0]["content"],"hello")
        self.assertEqual(responses[-1]["body"]["choices"][0]["message"]["content"],"antwort")
        self.assertEqual(len(recs[-1]["hash"]),64)

if __name__=="__main__":unittest.main()
