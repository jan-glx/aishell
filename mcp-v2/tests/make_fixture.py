"""Create throwaway OAuth test tokens in STAGING ONLY (never production DB)."""
import hashlib,json,os,sqlite3,secrets,time,grp
from pathlib import Path
p=Path('/var/lib/aishell-v2-stage/oauth.sqlite3')
assert p.parent==Path('/var/lib/aishell-v2-stage')
conn=sqlite3.connect(p)
conn.execute('CREATE TABLE IF NOT EXISTS tokens (id_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, expires REAL NOT NULL)')
conn.execute('DELETE FROM tokens')
credentials={}
for label,subject,scope,resource in [
 ('owner','test:owner',['aishell:control','aishell:files'],'https://aishell.gleixner.xyz/mcp'),
 ('guest','test:guest',['aishell:control','aishell:files'],'https://aishell.gleixner.xyz/mcp'),
 ('guest_control','test:guest',['aishell:control'],'https://aishell.gleixner.xyz/mcp'),
 ('guest_files','test:guest',['aishell:files'],'https://aishell.gleixner.xyz/mcp'),
 ('guest_wrong_audience','test:guest',['aishell:control'],'https://wrong.example/mcp'),
 ('guest_expired','test:guest',['aishell:control'],'https://aishell.gleixner.xyz/mcp'),
 ('unmapped','test:someone-else',['aishell:control'],'https://aishell.gleixner.xyz/mcp'),
]:
 token=secrets.token_urlsafe(48)
 expires=time.time()-1 if label=='guest_expired' else time.time()+1800
 conn.execute('INSERT INTO tokens VALUES (?,?,?,?)',(
  hashlib.sha256(token.encode()).hexdigest(),'access',json.dumps({
   'subject':subject,'client_id':'stage-only-client','scopes':scope,'resource':resource,
  }),expires))
 credentials[label]=token
conn.commit();conn.close()
file=Path('/run/aishell-v2-stage/fixture.json')
file.write_text(json.dumps(credentials))
os.chown(file,0,grp.getgrnam('aishell-mcp-test').gr_gid)
file.chmod(0o640)
p.chmod(0o600)
print('Prepared stage-only OAuth token fixtures: '+', '.join(credentials))
