"""Full OAuth → MCP (localhost shadow) → direct executor end-to-end test.

Registration, password login, short-lived tokens, privileged calls and revocation
are conducted against isolated staging services only (127.0.0.1:8767,8781).
No production OAuth clients, files, tokens, or nginx routes are changed.
"""
import base64, hashlib, httpx, json, re, secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE = 'http://127.0.0.1:8767'
PUBLIC = 'https://aishell.gleixner.xyz'
REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'
HEADERS = {'Content-Type':'application/json','Accept':'application/json, text/event-stream'}


def expect_response(response, status, label):
    assert response.status_code == status, (label, response.status_code, response.text[:350])
    print(label, 'HTTP', status)
    return response


def call_tool(c, token, name, args):
    result=expect_response(c.post(BASE+'/mcp',headers={**HEADERS,'Authorization':'Bearer '+token},json={
       'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':name,'arguments':args}
    }),200,name).json()['result']
    return result


def authorize(c, client, scopes, username='owner'):
    verifier=secrets.token_urlsafe(40)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state=secrets.token_urlsafe(16)
    result=expect_response(c.get(BASE+'/authorize',params={
        'response_type':'code','client_id':client['client_id'],'redirect_uri':REDIRECT,
        'scope':scopes,'state':state,'code_challenge':challenge,
        'code_challenge_method':'S256','resource':PUBLIC+'/mcp',
    }),302,'Authorization initiation')
    url=urlsplit(result.headers['location'])
    assert url.hostname=='aishell.gleixner.xyz' and url.path=='/oauth/consent'
    ticket=parse_qs(url.query)['ticket'][0]
    html=expect_response(c.get(BASE+'/oauth/consent',params={'ticket':ticket}),200,'Consent form')
    csrf=re.search(r'name="csrf" value="([^"]+)"',html.text).group(1)
    password_file = ('/root/.config/aishell-mcp-oauth/owner-password'
                     if username == 'owner' else
                     '/root/.config/aishell-v2/stage-guest-password')
    pw=Path(password_file).read_text().strip()
    form_headers={'Origin':PUBLIC,'Cookie':'aishell_oauth_csrf='+csrf}
    if username == 'guest':
        # A guest cannot authenticate as the owner by changing only the name.
        wrong=c.post(BASE+'/oauth/consent',data={
            'ticket':ticket,'csrf':csrf,'username':'owner','password':pw,'decision':'approve',
        },headers=form_headers)
        assert wrong.status_code==403,(wrong.status_code,wrong.text[:300])
        print('PASS guest password cannot authenticate as root owner')
    grant=expect_response(c.post(BASE+'/oauth/consent',data={
        'ticket':ticket,'csrf':csrf,'username':username,'password':pw,'decision':'approve',
    },headers=form_headers),303,'Owner approval')
    target=urlsplit(grant.headers['location'])
    assert target.hostname=='chatgpt.com' and target.path=='/connector_platform_oauth_redirect'
    result=parse_qs(target.query)
    assert result['state']==[state] and result['iss']==[PUBLIC]
    code=result['code'][0]
    tokens=expect_response(c.post(BASE+'/token',data={
      'grant_type':'authorization_code','client_id':client['client_id'],
      'client_secret':client['client_secret'],'code':code,
      'redirect_uri':REDIRECT,'code_verifier':verifier,'resource':PUBLIC+'/mcp',
    }),200,'OAuth token exchange').json()
    assert tokens['expires_in']==900 and tokens['refresh_token']
    return tokens


def main():
    marker='v2_shadow_'+secrets.token_hex(5)
    filename=marker+'.txt'
    with httpx.Client(timeout=80,follow_redirects=False) as c:
        client=expect_response(c.post(BASE+'/register',json={
          'redirect_uris':[REDIRECT], 'token_endpoint_auth_method':'client_secret_post',
          'grant_types':['authorization_code','refresh_token'],'response_types':['code'],
          'scope':'aishell:ping aishell:control aishell:files',
          'client_name':'AIshell v2 shadow internal integration test',
        }),201,'Shadow DCR client').json()
        try:
            login=authorize(c,client,'aishell:ping aishell:control aishell:files')
            token=login['access_token']
            who=call_tool(c,token,'runCommand',{'command':'id -un'})
            assert not who['isError'] and who['structuredContent']['stdout'].strip()=='root',who
            print('PASS OAuth → MCP → v2 direct root executor (no legacy HTTP)')
            text=call_tool(c,token,'transferTextFile',{'filename':filename,'content':'v2 shadow file payload'})
            assert not text['isError'] and text['structuredContent']['unix_user']=='root',text
            output=call_tool(c,token,'runCommand',{'command':'printf v2-shadow-export > ~/output/'+filename})
            assert not output['isError'] and output['structuredContent']['exit_code']==0,output
            signed=call_tool(c,token,'getFile',{'filename':filename})
            assert not signed['isError'],signed
            url=urlsplit(signed['structuredContent']['openaiFileResponse'][0])
            assert url.hostname=='aishell.gleixner.xyz' and url.path.startswith('/mcp-v2/download/')
            fetched=expect_response(c.get('http://127.0.0.1:8781'+url.path+'?'+url.query),200,'v2 signed download')
            assert fetched.content==b'v2-shadow-export'
            print('PASS OAuth → MCP → v2 file write + signed read')
            low=authorize(c,client,'aishell:ping')
            denied=call_tool(c,low['access_token'],'runCommand',{'command':'id -u'})
            assert denied['isError'] and 'mcp/www_authenticate' in denied['_meta'],denied
            print('PASS ping-only OAuth subject cannot run root command')
            guest=authorize(c,client,'aishell:ping aishell:control aishell:files',username='guest')
            guest_token=guest['access_token']
            guest_who=call_tool(c,guest_token,'runCommand',{'command':'id -un; id -u'})
            assert not guest_who['isError']
            assert guest_who['structuredContent']['stdout'].splitlines()[0]=='aishell-v2-test',guest_who
            assert guest_who['structuredContent']['unix_user']=='aishell-v2-test'
            guest_unix=call_tool(c,guest_token,'runCommand',{'command':'id -un','unix_user':'root'})
            assert guest_unix['structuredContent']['stdout'].strip()=='aishell-v2-test'
            secret=call_tool(c,guest_token,'runCommand',{'command':'cat /etc/aishell-v2/downloads.key'})
            assert secret['structuredContent']['exit_code'] != 0 and not secret['structuredContent']['stdout']
            print('PASS independently logged-in guest OAuth identity executes only as unprivileged UID')
            # Refresh tokens must retain the original subject and never upgrade
            # the Unix account. Old rotated refresh credentials must not replay.
            refresh_data={'grant_type':'refresh_token','client_id':client['client_id'],
                          'client_secret':client['client_secret'],
                          'refresh_token':guest['refresh_token'], 'resource':PUBLIC+'/mcp'}
            guest_rotated=expect_response(c.post(BASE+'/token',data=refresh_data),200,
                                         'Guest refresh rotation').json()
            assert guest_rotated['refresh_token'] != guest['refresh_token']
            replay=c.post(BASE+'/token',data=refresh_data)
            assert replay.status_code==400,(replay.status_code,replay.text[:160])
            next_command=call_tool(c,guest_rotated['access_token'],'runCommand',{'command':'id -un'})
            assert not next_command['isError']
            assert next_command['structuredContent']['stdout'].strip()=='aishell-v2-test'
            print('PASS guest refresh token remains unprivileged and replay rejected')
            revoked=expect_response(c.post(BASE+'/revoke',data={
                'token':token, 'client_id':client['client_id'],
                'client_secret':client['client_secret'],
            }),200,'Revoke v2 access token')
            response=c.post(BASE+'/mcp',headers={**HEADERS,'Authorization':'Bearer '+token},
                json={'jsonrpc':'2.0','id':55,'method':'tools/call',
                      'params':{'name':'runCommand','arguments':{'command':'id -u'}}})
            assert response.status_code==401,(response.status_code,response.text[:200])
            print('PASS revoked privileged token denied HTTP 401')
        finally:
            # Use an isolated fixture fallback for cleanup if the previous token
            # was already revoked. No unbounded rm commands.
            import subprocess
            for p in (Path('/root/input')/filename, Path('/root/output')/filename):
                try: p.unlink()
                except FileNotFoundError: pass
            # All registered staging OAuth clients/tokens are cleared by the
            # external test-cleanup step after this script completes.
    print('PASS: full shadow MCP integration suite')

if __name__=='__main__':
    main()
