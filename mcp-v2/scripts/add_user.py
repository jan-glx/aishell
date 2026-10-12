"""Administrator-only user registration for aishell v2 (no shell-account creation).

Usage: sudo /opt/aishell-v2/.venv/bin/python scripts/add_user.py LOGIN UNIX_USER
The Linux user must already exist. Password prompted twice (never CLI/argv).
Updates OAuth credential mapping + root executor authorization mapping together.
Restart both services after reviewing changes; revoke older grants if applicable.
"""
import getpass
import grp
import json
import os
import pwd
import stat
import sys
from pathlib import Path
from argon2 import PasswordHasher


def main():
    if os.geteuid() != 0 or len(sys.argv) != 3:
        raise SystemExit('Run as root: add_user.py LOGIN EXISTING_UNIX_USER')
    login, unix_name = sys.argv[1:]
    if (not login.isascii() or not login.replace('_','').replace('-','').isalnum()
            or len(login) > 50 or not login):
        raise SystemExit('Login must be <=50 ASCII alphanumeric/underscore/hyphen chars')
    if login == 'owner':
        raise SystemExit('Owner login managed separately')
    account = pwd.getpwnam(unix_name)
    if account.pw_uid == 0:
        raise SystemExit('This tool never grants root access')
    if not Path(account.pw_dir).is_dir():
        raise SystemExit('Unix user home directory is missing')
    secret = getpass.getpass('New aishell login password (at least 16 chars): ')
    if len(secret) < 16 or secret != getpass.getpass('Confirm password: '):
        raise SystemExit('Password too short or confirmation mismatch')
    oauth_path = Path('/etc/aishell-v2/auth-users.json')
    executor_path = Path('/etc/aishell-v2/config.json')
    oauth = json.loads(oauth_path.read_text())
    executor = json.loads(executor_path.read_text())
    subject = 'user:' + login
    if login in oauth['users'] or subject in executor['principals']:
        raise SystemExit('User already exists in mapping')
    if any(row['unix_user'] == unix_name for row in executor['principals'].values()):
        raise SystemExit('Linux user is already mapped; one account per Unix UID')
    permitted = ['aishell:ping', 'aishell:control', 'aishell:files']
    oauth['users'][login] = {
        'subject': subject,
        'display_name': login,
        'password_hash': PasswordHasher(time_cost=3,memory_cost=32768,parallelism=1).hash(secret),
        'allowed_scopes': permitted,
    }
    executor['principals'][subject] = {
        'unix_user': unix_name, 'allow_root': False,
        'allowed_scopes': ['aishell:control','aishell:files'],
    }
    # Atomic replace. On disk configs are independent, so stop both services
    # before modifying (this script does not restart anything automatically).
    for path, payload, gid, mode in [
        (oauth_path, oauth, grp.getgrnam(executor['socket_group']).gr_gid, 0o640),
        (executor_path, executor, 0, 0o600),
    ]:
        tmp = path.with_suffix(path.suffix+'.tmp')
        with open(tmp, 'x') as f:
            json.dump(payload,f,indent=2)
            f.write('\n')
        os.chown(tmp,0,gid)
        os.chmod(tmp,mode)
        os.replace(tmp,path)
    print(f'Registered {login!r} -> {unix_name!r} in both configurations.')
    print('Review mappings and restart MCP and executor services deliberately.')

if __name__ == '__main__': main()
