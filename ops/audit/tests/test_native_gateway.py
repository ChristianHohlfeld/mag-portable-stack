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
        cls.temp=tempfile.TemporaryDirectory(prefix="mag-audit-fixed-q4-")
        cls.dir=pathlib.Path(cls.temp.name)
        cls.q4p,cls.iq3p,cls.gwp=port(),port(),port()
        cls.q4=http.server.ThreadingHTTPServer(("127.0.0.1",cls.q4p),Q4)
        cls.iq3=http.server.ThreadingHTTPServer(("127.0.0.1",cls.iq3p),IQ3)
        for server in (cls.q4,cls.iq3):threading.Thread(target=server.serve_forever,daemon=True).start()
        cls.env=dict(os.environ,MAG_AUDIT_LISTEN_PORT=str(cls.gwp),MAG_MODEL_Q4_PORT=str(cls.q4p),
            MAG_MODEL_IQ3_PORT=str(cls.iq3p),MAG_AUDIT_LOG_DIR=str(cls.dir/"logs"),
            MAG_AUDIT_SECRETS_FILE=str(cls.dir/"missing-secrets"),
            MAG_MODEL_CONTROL_SOCKET=str(cls.dir/"control.sock"),
            MAG_MODEL_SUPERVISOR_SOCKET=str(cls.dir/"supervisor.sock"))
        cls.proc=subprocess.Popen([str(ROOT/"build/mag-audit-gateway")],env=cls.env,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            if cls.proc.poll() is not None:raise RuntimeError(cls.proc.stderr.read().decode())
            try:
                if request(cls.gwp)[0]==200:break
            except (ConnectionError,OSError):pass
            time.sleep(.05)
        else:raise RuntimeError("Gateway not ready")
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=3);cls.proc.stderr.close()
        for server in (cls.q4,cls.iq3):server.shutdown();server.server_close()
        cls.temp.cleanup()
    def test_01_iq3_routes_without_touching_q4(self):
        Q4.calls.clear();IQ3.calls.clear()
        code,j=request(self.gwp,"POST","/v1/chat/completions",{"model":"deepseek-v4-flash-0731-iq3","messages":[]})
        self.assertEqual(code,200)
        self.assertEqual(j["model"],"deepseek-v4-flash-0731-iq3")
        self.assertEqual(len(Q4.calls),0)
        self.assertEqual(len(IQ3.calls),1)
        self.assertEqual(IQ3.calls[0][1]["model"],"deepseek-v4-flash-0731-iq3")
        self.assertFalse((self.dir/"control.sock").exists())
        self.assertFalse((self.dir/"supervisor.sock").exists())
        code,listed=request(self.gwp,"GET","/v1/models")
        self.assertEqual(code,200)
        ids=sorted(row["id"] for row in listed["data"])
        self.assertEqual(ids,["deepseek-v4-flash-0731","deepseek-v4-flash-0731-iq3"])
        self.assertTrue(all(row["ready"] for row in listed["data"]))
    def test_02_no_switch_endpoint_on_gateway(self):
        Q4.calls.clear();IQ3.calls.clear()
        code,_=request(self.gwp,"PUT","/models",{"id":"iq3"})
        self.assertNotEqual(code,202)
        code,_=request(self.gwp,"POST","/v1/chat/completions",{"model":"x"})
        self.assertEqual(code,200)
        self.assertEqual(IQ3.calls,[])
    def test_03_sse_audit_hash_and_tool_calls(self):
        import sys
        code,_=request(self.gwp,"POST","/v1/chat/completions",
            {"model":"x","stream":True,"messages":[{"role":"user","content":"stream"}]})
        self.assertEqual(code,200)
        events=[json.loads(l) for f in (self.dir/"logs").rglob("events.jsonl") for l in f.read_text().splitlines()]
        responses=[e for e in events if e.get("type")=="llm_response" and e.get("streamed")]
        self.assertTrue(responses)
        msg=responses[-1]["body"]["choices"][0]["message"]
        self.assertEqual(msg["content"],"Hallo Welt")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"],"run_command")
        self.assertEqual(responses[-1]["model_variant"],"q4")
        verify=subprocess.run([sys.executable,str(ROOT/"ops/audit/mag-audit-tail"),"--verify"],
          env=dict(os.environ,MAG_AUDIT_LOG_DIR=str(self.dir/"logs")),capture_output=True,text=True)
        self.assertEqual(verify.returncode,0,verify.stdout+verify.stderr)

if __name__=="__main__":unittest.main()
