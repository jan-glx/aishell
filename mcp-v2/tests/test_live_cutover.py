"""One-time production MCP cutover validation: owner OAuth → v2 executor.

Only run deliberately: AISHELL_CONFIRM_LIVE_TEST=yes .../python test_live_cutover.py
Uses a temporary ChatGPT-format OAuth client in the LIVE DB, creates two
small disposable files and a disposable v2 tmux session. Never prints secrets.
Deletes only its own synthetic OAuth client/tokens and artifacts on exit.
Does not touch unrelated FastAPI applications or production ChatGPT client.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs,urlsplit

import httpx

BASE='https://aishell.gleixner.xyz'
RESOURCE=BASE+'/mcp'
REDIRECT='https://chatgpt.com/connector_platform_oauth_redirect'
DB='/var/lib/aishell-mcp-test/oauth.sqlite3'
OWNER_PASSWORD='/root/.config/aishell-mcp-oauth/owner-password'
CLIENT_NAME='aishell-v2-live-cutover-self-test'
HEADERS={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}


def expect(r,status,what):
    assert r.status_code==status,(what,r.status_code,r.text[:260])
    print(what, 'HTTP',status)
    return r


def oauth_grant(client: httpx.Client,clientinfo:dict,scope:str) -> dict:
    verifier=secrets.token_urlsafe(40)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state=secrets.token_urlsafe(16)
    r=expect(client.get(BASE+'/authorize',params={
        'response_type':'code','client_id':clientinfo['client_id'],'redirect_uri':REDIRECT,
        'scope':scope,'state':state,'code_challenge':challenge,
        'code_challenge_method':'S256','resource':RESOURCE,
    }),302,'Authorization redirect')
    target=urlsplit(r.headers['location'])
    assert target.netloc=='aishell.gleixner.xyz' and target.path=='/oauth/consent'
    ticket=parse_qs(target.query)['ticket'][0]
    consent=expect(client.get(BASE+'/oauth/consent',params={'ticket':ticket}),200,'OAuth owner consent')
    assert consent.headers.get('referrer-policy')=='origin'
    assert 'name="username"' in consent.text
    csrf=re.search(r'name="csrf" value="([^"]+)"',consent.text)
    assert csrf
    pwd=Path(OWNER_PASSWORD).read_text().strip()
    r=expect(client.post(BASE+'/oauth/consent',data={
        'ticket':ticket,'csrf':csrf.group(1),'username':'owner',
        'password':pwd,'decision':'approve'
    },headers={'Origin':BASE}),303,'Owner consent approval')
    redirect=urlsplit(r.headers['location'])
    assert redirect.netloc=='chatgpt.com' and redirect.path=='/connector_platform_oauth_redirect'
    args=parse_qs(redirect.query)
    assert args['state']==[state] and args['iss']==[BASE]
    code=args['code'][0]
    tok=expect(client.post(BASE+'/token',data={
        'grant_type':'authorization_code','client_id':clientinfo['client_id'],
        'client_secret':clientinfo['client_secret'],'code':code,
        'redirect_uri':REDIRECT,'code_verifier':verifier,'resource':RESOURCE,
    }),200,'Token exchange').json()
    assert tok['access_token'] and tok['refresh_token']
    return tok


def call(client,token,name,args,modern=False):
    payload={'jsonrpc':'2.0','id':17,'method':'tools/call',
             'params':{'name':name,'arguments':args}}
    headers={**HEADERS,'Authorization':'Bearer '+token}
    if modern:
        payload['params']['_meta']={'io.modelcontextprotocol/protocolVersion':'2026-07-28'}
        headers['MCP-Protocol-Version']='2026-07-28'
    result=expect(client.post(RESOURCE,headers=headers,json=payload),200,name+' MCP call').json()
    return result['result']


def clean_synthetic_registration():
    with sqlite3.connect(DB, timeout=8) as con:
        con.execute('BEGIN IMMEDIATE')
        ids=[]
        for client_id,data in con.execute('SELECT id,data FROM clients').fetchall():
            if json.loads(data).get('client_name')==CLIENT_NAME:
                ids.append(client_id)
        for cid in ids:
            for table in ('tokens','codes','tickets'):
                con.execute(f'DELETE FROM {table} WHERE json_extract(data,"$.client_id")=?',(cid,))
            con.execute('DELETE FROM clients WHERE id=?',(cid,))
        print('Cleaned synthetic live OAuth clients:',len(ids))
        assert con.execute('SELECT COUNT(*) FROM clients').fetchone()[0]>=1


def main():
    assert os.geteuid()==0, 'Read-only access to owner-password file required'
    assert os.environ.get('AISHELL_CONFIRM_LIVE_TEST')=='yes', 'Explicit live test confirmation env missing'
    unique='aishell_v2_live_'+secrets.token_hex(6)
    inputfile=Path('/root/input')/(unique+'.txt')
    outputfile=Path('/root/output')/(unique+'.txt')
    tmux=unique.replace('-','_')
    with httpx.Client(timeout=50,follow_redirects=False) as client:
        try:
            # Modern ChatGPT discovery, public tool metadata and scopes.
            discovery={'jsonrpc':'2.0','id':1,'method':'server/discover',
                       'params':{'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28'}}}
            r=expect(client.post(RESOURCE,headers={**HEADERS,
                'MCP-Protocol-Version':'2026-07-28'},json=discovery),200,'Modern MCP discover')
            assert '2026-07-28' in r.text
            r=expect(client.post(RESOURCE,headers=HEADERS,json={
                'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}),200,'Tool discovery')
            tools=r.json()['result']['tools']
            assert {t['name'] for t in tools}=={'ping_aishell','runCommand','read_tmux_buffer',
                  'send_tmux_keys','transferFile','transferTextFile','getFile'}
            print('PASS seven v2 tool definitions')
            meta=expect(client.get(BASE+'/.well-known/oauth-authorization-server'),200,'OAuth metadata').json()
            assert set(['aishell:ping','aishell:control','aishell:files']).issubset(meta['scopes_supported'])
            info=expect(client.post(BASE+'/register',json={
                'redirect_uris':[REDIRECT],'token_endpoint_auth_method':'client_secret_post',
                'grant_types':['authorization_code','refresh_token'],'response_types':['code'],
                'scope':'aishell:ping aishell:control aishell:files',
                'client_name':CLIENT_NAME,
            }),201,'Synthetic ChatGPT-format client registration').json()
            access=oauth_grant(client,info,'aishell:ping aishell:control aishell:files')
            bearer=access['access_token']
            ping=call(client,bearer,'ping_aishell',{})
            assert not ping['isError'] and 'AISHELL_MCP_PLUS_TEST_OK' in ping['content'][0]['text']
            who=call(client,bearer,'runCommand',{'command':'id -un; id -u'},modern=True)
            assert not who['isError'] and who['structuredContent']['stdout'].splitlines()==['root','0'],who
            assert who['structuredContent']['unix_user']=='root'
            print('PASS HTTPS OAuth → MCP → direct v2 root executor')
            pane=call(client,bearer,'read_tmux_buffer',{'session':tmux})
            assert not pane['isError'] and pane['structuredContent']['unix_user']=='root',pane
            sent=call(client,bearer,'send_tmux_keys',{'session':tmux,
                'input':['printf aishell-v2-live-tmux-ok','Enter']})
            assert not sent['isError'],sent
            time.sleep(.3)
            pane=call(client,bearer,'read_tmux_buffer',{'session':tmux})
            assert not pane['isError'] and 'aishell-v2-live-tmux-ok' in pane['structuredContent']['output'],pane
            print('PASS v2 tmux send and read')
            saved=call(client,bearer,'transferTextFile',{
                'filename':inputfile.name,'content':'AISHELL_V2_TEXT_FILE_OK'})
            assert not saved['isError'] and saved['structuredContent']['bytes_written']==len('AISHELL_V2_TEXT_FILE_OK'),saved
            assert inputfile.read_text()=='AISHELL_V2_TEXT_FILE_OK'
            outputfile.write_text('AISHELL_V2_FILE_DOWNLOAD_OK\n')
            signed=call(client,bearer,'getFile',{'filename':outputfile.name})
            assert not signed['isError'],signed
            url=signed['structuredContent']['openaiFileResponse'][0]
            assert url.startswith(BASE+'/mcp-v2/download/')
            downloaded=expect(client.get(url),200,'Signed file download')
            assert downloaded.content==b'AISHELL_V2_FILE_DOWNLOAD_OK\n'
            print('PASS authenticated v2 file write/download')
            only_ping=oauth_grant(client,info,'aishell:ping')
            denied=call(client,only_ping['access_token'],'runCommand',{'command':'id -u'})
            assert denied['isError'] and 'mcp/www_authenticate' in denied.get('_meta',{})
            print('PASS OAuth ping-only scope cannot execute root commands')
            revoked=expect(client.post(BASE+'/revoke',data={
                'token':bearer,'client_id':info['client_id'],
                'client_secret':info['client_secret']}),200,'Revoke privileged grant')
            bad=client.post(RESOURCE,headers={**HEADERS,'Authorization':'Bearer '+bearer},
                json={'jsonrpc':'2.0','id':4,'method':'tools/call',
                      'params':{'name':'runCommand','arguments':{'command':'id -u'}}})
            assert bad.status_code==401, (bad.status_code,bad.text[:100])
            print('PASS revoked bearer cannot call v2 executor')
        finally:
            import subprocess
            subprocess.run(['tmux','-L','aishell-v2','kill-session','-t',tmux],
                           check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            inputfile.unlink(missing_ok=True)
            outputfile.unlink(missing_ok=True)
            clean_synthetic_registration()
    print('PASS: public HTTPS live v2 MCP complete')

if __name__=='__main__': main()
