"""Exercise actual temporary root PTY socket as isolated bot, including teardown."""
import os,socket,subprocess,time,pathlib,unittest,signal,pwd
SOCK="/run/mag-root-shell.sock"
LEASE="/run/mag-root-shell.heartbeat"
BIN=str(pathlib.Path(__file__).resolve().parents[2]/"build/mag-root-console")
class RootGuestPTY(unittest.TestCase):
 def test_interactive_root_and_revoke(self):
  self.assertEqual(os.geteuid(),0,"Run with sudo on isolated CI runner")
  try:pwd.getpwnam("bot")
  except KeyError:subprocess.run(["useradd","-r","-M","-s","/bin/bash","bot"],check=True)
  pathlib.Path(LEASE).write_text(str(int(time.time())))
  proc=subprocess.Popen([BIN])
  try:
   for _ in range(60):
    if os.path.exists(SOCK):break
    time.sleep(.05)
   else:self.fail("Root PTY Unix socket did not appear")
   script='''import socket,time
s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
s.settimeout(2)
s.connect("/run/mag-root-shell.sock")
s.sendall(b"Iid -u"+bytes([13]))
out=b""
until=time.monotonic()+6
while time.monotonic()<until:
 try:buf=s.recv(700)
 except socket.timeout:continue
 if not buf:break
 if buf.startswith(b"O"):out+=buf[1:]
 if bytes([48,13]) in out or bytes([48,10]) in out:break
assert bytes([48,13]) in out or bytes([48,10]) in out,repr(out[-1000:])
print("root terminal PTY verified")
'''
   result=subprocess.run(["runuser","-u","bot","--","python3","-c",script],capture_output=True,text=True,timeout=12)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
  finally:
   proc.send_signal(signal.SIGTERM)
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   pathlib.Path(LEASE).unlink(missing_ok=True)
   pathlib.Path(SOCK).unlink(missing_ok=True)
  self.assertFalse(os.path.exists(SOCK),"Revoked root socket still exists")
if __name__=="__main__":unittest.main(verbosity=2)
