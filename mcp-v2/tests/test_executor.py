"""Unprivileged end-to-end tests of v2 root executor via Unix socket.

Requires synthetic STAGING fixtures. Does not access live OAuth credentials.
"""
import hashlib,http.client,json,os,socket,secrets,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlunsplit,urlencode
SOCK='/run/aishell-v2-stage/control.sock'
TOKENS=json.loads(Path('/run/aishell-v2-stage/fixture.json').read_text())
MARK='aishell_v2_test_'+secrets.token_hex(5)

def action(op,args=None,who='guest', expected=True):
 sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
 sock.settimeout(75)
 sock.connect(SOCK)
 sock.sendall((json.dumps({'op':op,'arguments':args or {},
     'access_token': TOKENS.get(who,'fake-invalid-token-with-at-least-40-chars-123456')})+'\n').encode())
 chunks=bytearray()
 while not chunks.endswith(b'\n'):
  b=sock.recv(65536)
  if not b: break
  chunks.extend(b)
 sock.close()
 assert chunks.endswith(b'\n'),f'No complete reply for {op}'
 result=json.loads(chunks)
 assert result['ok'] is expected,f'{op} as {who}: {result}'
 return result.get('result', result.get('error'))

def download(url):
 u=urlsplit(url)
 assert u.scheme=='https' and u.netloc=='aishell.gleixner.xyz'
 conn=http.client.HTTPConnection('127.0.0.1',8781,timeout=8)
 conn.request('GET',u.path+'?'+u.query)
 resp=conn.getresponse()
 data=resp.read(150000)
 result=(resp.status,data,dict(resp.getheaders()))
 conn.close()
 return result

# Token authorization is INDEPENDENT of the caller's Unix UID.
for who in ('guest_wrong_audience','guest_expired','unmapped','invalid'):
 action('runCommand',{'command':'id'},who=who,expected=False)
print('PASS: fake, expired, unmapped and wrong-audience tokens denied')
action('getFile',{'filename':'none.txt'},who='guest_control',expected=False)
action('runCommand',{'command':'id'},who='guest_files',expected=False)
print('PASS: control and file scopes separated')

owner=action('runCommand',{'command':'id -u; printf "$USER|$HOME"'},who='owner')
assert owner['unix_user']=='root' and owner['stdout'].startswith('0\n') and 'root|/root' in owner['stdout'],owner
print('PASS: owner gets root identity (only with owner OAuth subject)')

guest=action('runCommand',{'command':'id -u; id -un; printf "%s" "$HOME"'})
assert guest['unix_user']=='aishell-v2-test' and guest['stdout'].startswith(''+str(os.getuid())+'\n') is False,guest
assert guest['stdout'].splitlines()[1]=='aishell-v2-test',guest
assert '/var/lib/aishell-v2-test' in guest['stdout'],guest
print('PASS: guest process runs as a distinct unprivileged Unix UID')

secret=action('runCommand',{'command':'cat /etc/aishell-v2/downloads.key'})
assert secret['exit_code'] != 0 and not secret['stdout'],secret
print('PASS: unprivileged guest cannot read v2 signing secret')

# These are TWO different token subjects. tool arguments cannot select a UID.
command=action('runCommand',{'command':'id -un','unix_user':'root'})
assert command['stdout'].strip()=='aishell-v2-test'
print('PASS: spoofed Unix account in tool arguments ignored')

# Separate tmux daemon -L aishell-v2, owned by guest. No interaction with legacy.
session='v2stage_'+secrets.token_hex(3)
t1=action('read_tmux_buffer',{'session':session})
assert t1['unix_user']=='aishell-v2-test',t1
action('send_tmux_keys',{'session':session,'input':['printf v2-tmux-success','Enter']})
time.sleep(.25)
t2=action('read_tmux_buffer',{'session':session})
assert 'v2-tmux-success' in t2['output'],t2
root_pane=action('read_tmux_buffer',{'session':session},who='owner')
assert 'v2-tmux-success' not in root_pane['output'],root_pane
print('PASS: tmux sessions are independently namespaced by Unix UID')

target=MARK+'.txt'
written=action('transferTextFile',{'filename':target,'content':'v2 sandbox text payload'})
assert written['unix_user']=='aishell-v2-test' and written['bytes_written']==23,written
content=action('runCommand',{'command':'cat ~/input/'+target})
assert content['stdout']=='v2 sandbox text payload',content
root_get=action('runCommand',{'command':'test -e /root/input/'+target+'; echo $?'},who='owner')
assert root_get['stdout'].strip()=='1',root_get
print('PASS: per-user file writing and cross-account input isolation')

out=action('runCommand',{'command':'printf v2-download-payload > ~/output/'+target})
assert out['exit_code']==0,out
link=action('getFile',{'filename':target})
u=link['openaiFileResponse'][0]
status,body,headers=download(u)
assert status==200 and body==b'v2-download-payload',(status,body)
assert headers.get('Cache-Control')=='no-store'
print('PASS: signed output download served by independent loopback HTTP listener')

bad=u.replace('signature=','signature=bad')
assert download(bad)[0]==403
u_parts=urlsplit(u)
q=parse_qs(u_parts.query)
q['expires']=['1']
bad_expiry=urlunsplit((u_parts.scheme,u_parts.netloc,u_parts.path,urlencode(q,doseq=True),''))
assert download(bad_expiry)[0]==403
print('PASS: tampered and expired downloads rejected')

# Attempt path traversal and symlink read (protected by basename and O_NOFOLLOW).
action('getFile',{'filename':'../../etc/passwd'},expected=False)
action('transferTextFile',{'filename':'../unsafe','content':'no'},expected=False)
action('transferFile',{'openaiFileIdRefs':[
    {'name':MARK+'.txt','download_link':'http://127.0.0.1:8781/mcp'}]},expected=False)
print('PASS: path traversal and SSRF rejected')

action('runCommand',{'command':'ln -s /etc/passwd ~/output/'+MARK+'.symlink'})
action('getFile',{'filename':MARK+'.symlink'},expected=False)
print('PASS: symlinked output file rejected')

# Cleanup test artifacts, leaving both real applications and original API alone.
action('runCommand',{'command':'tmux -L aishell-v2 kill-session -t '+session+' 2>/dev/null || true; rm -f ~/input/'+target+' ~/output/'+target+' ~/output/'+MARK+'.symlink'})
action('runCommand',{'command':'tmux -L aishell-v2 kill-session -t '+session+' 2>/dev/null || true'},who='owner')
print('PASS: all staging tests complete')
