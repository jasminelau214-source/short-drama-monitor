"""Disposable loopback-only PostgreSQL harness, with no DSN/env target input."""
from pathlib import Path
import os
import secrets
import socket
import subprocess
import tempfile


class LocalPostgresFixture:
    database = 'jsm_v2_fixture'
    admin = 'jsm_fixture_admin'
    worker = 'jsm_fixture_worker'

    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir).resolve(strict=True)
        self.suffix = '.exe' if os.name == 'nt' else ''
        for name in ('postgres','initdb','pg_ctl','psql'):
            if not (self.bin_dir/(name+self.suffix)).is_file():
                raise ValueError('Explicit PostgreSQL binaries required')
        self.directory = None
        self.started = False
        self.passwords = {self.admin:secrets.token_hex(24), self.worker:secrets.token_hex(24)}

    def _env(self, user=None):
        env = {key:value for key,value in os.environ.items() if not key.upper().startswith('PG')}
        env.update(PGCLIENTENCODING='UTF8',PGCONNECT_TIMEOUT='5')
        if user: env['PGPASSWORD'] = self.passwords[user]
        return env

    def command(self, name, *args, text=None, user=None, check=True):
        # On Windows the detached server can inherit pg_ctl pipe handles.
        # Capturing pg_ctl pipes can keep communicate() blocked after it exits.
        output = {'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL} if name=='pg_ctl' else {'capture_output':True}
        result = subprocess.run([str(self.bin_dir/(name+self.suffix)),*map(str,args)],
                                input=text,**output,encoding='utf-8',errors='replace',
                                timeout=45,env=self._env(user),
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if check and result.returncode:
            error = result.stderr or result.stdout or 'see disposable server log'
            for password in self.passwords.values(): error=error.replace(password,'[fixture password]')
            raise RuntimeError(f'{name} failed: {error[-5000:]}')
        return result

    def start(self):
        # Preserve the directory if shutdown fails; never let finalization
        # remove files beneath a still-running server.
        self.directory = tempfile.TemporaryDirectory(prefix='jsm-pg-v2-',delete=False)
        self.root = Path(self.directory.name).resolve()
        self.cluster = self.root/'cluster'
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0)); self.port=probe.getsockname()[1]
            password_file = self.root/'bootstrap-password'
            password_file.write_text(self.passwords[self.admin]+'\n',encoding='utf-8')
            try:
                self.command('initdb','-D',self.cluster,'-U',self.admin,'-E','UTF8','--locale=C',
                             '--auth=scram-sha-256','--pwfile',password_file)
            finally: password_file.unlink(missing_ok=True)
            # Not registered as an OS service; cannot listen on a public IP.
            options=f'-h 127.0.0.1 -p {self.port} -c jsm.fixture_scope=C2_SYNTHETIC_ONLY -c statement_timeout=15000 -c lock_timeout=10000'
            self.command('pg_ctl','-D',self.cluster,'-l',self.root/'server.log','-o',options,'-w','-t','30','start')
            self.started = True
            self.sql('CREATE DATABASE jsm_v2_fixture;',database='postgres')
            self.sql("CREATE ROLE jsm_fixture_worker LOGIN PASSWORD '"+self.passwords[self.worker]+"';")
            self.server_version = self.sql('SHOW server_version;')
            assert self.sql('SHOW listen_addresses;')=='127.0.0.1'
            assert Path(self.sql('SHOW data_directory;')).resolve()==self.cluster
            return self
        except BaseException:
            self.close()
            raise

    def sql(self, text, *, worker=False, database=None, check=True):
        if not self.started:
            raise RuntimeError('Own temporary server must be running')
        database = database or self.database
        if database not in {self.database,'postgres'}: raise ValueError('Fixture databases only')
        user = self.worker if worker else self.admin
        result=self.command('psql','-X','-qAt','-h','127.0.0.1','-p',self.port,'-U',user,
                            '-d',database,'--set','ON_ERROR_STOP=1',text=text,user=user,check=check)
        return result.stdout.strip() if check else result

    def close(self):
        if not self.started and self.directory and self.cluster.exists():
            # A pg_ctl timeout may leave its child running. Check only the
            # newly owned cluster, never a service name or process-wide list.
            self.started = self.command('pg_ctl','-D',self.cluster,'status',check=False).returncode==0
        if self.started:
            self.command('pg_ctl','-D',self.cluster,'-w','-t','30','-m','fast','stop')
            self.started = False
        if self.directory:
            # TemporaryDirectory owns this freshly created path. No computed
            # repository or existing cluster path is ever recursively removed.
            if not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise RuntimeError('Temporary path boundary changed')
            self.directory.cleanup()
            self.directory = None

