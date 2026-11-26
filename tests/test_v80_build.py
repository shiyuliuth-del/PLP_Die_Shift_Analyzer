"""Reproduce the desktop backend's inherited Windows log handle at exit."""
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch


def worker(config):
    state=json.loads(Path(config).read_text(encoding='utf8'))
    with socket.create_connection(('127.0.0.1',state['port'])) as conn:
        print('native process owns batch.log',flush=True)
        hello=json.dumps(dict(token=state['token'],version='test',self_check=True)).encode()
        conn.sendall(struct.pack('!I',len(hello))+hello)
        while conn.recv(1024):
            pass


def driver(explicit):
    from die_shift_studio import native_backend
    popen=subprocess.Popen
    script=str(Path(__file__).resolve())
    def spawn(command,**kwargs):
        return popen([sys.executable,script,'--worker',command[-1].split('=',1)[1]],**kwargs)
    with patch.object(native_backend.subprocess,'Popen',side_effect=spawn):
        native_backend._backend=native_backend.DesktopBackend(sys.executable)
    directory=native_backend._backend.directory
    print(str(directory),flush=True)
    assert (directory/'batch.log').is_file()
    if explicit:
        native_backend.close()
        assert not directory.exists()


class DesktopBuildCleanupTests(unittest.TestCase):
    def test_child_and_inherited_log_close_before_temp_cleanup(self):
        for mode in ('explicit','atexit'):
            with self.subTest(mode=mode):
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--driver',mode],
                                      capture_output=True,text=True,encoding='utf8',timeout=20,check=True)
                temporary=Path(result.stdout.splitlines()[-1])
                self.assertFalse(temporary.exists())
                self.assertEqual(result.stderr,'')


if __name__=='__main__':
    if sys.argv[1]=='--worker':
        worker(sys.argv[2])
    elif sys.argv[1]=='--driver':
        driver(sys.argv[2]=='explicit')
