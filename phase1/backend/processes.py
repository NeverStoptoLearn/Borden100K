"""Windows kill-on-close Job Object contains children AND grandchildren."""
import os
import subprocess
import time
import psutil


def alive(pid, birth):
    try:
        p = psutil.Process(pid)
        return abs(p.create_time() - birth) < .01 and p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def stop_tree(pid, birth):
    if not alive(pid, birth):
        return
    root = psutil.Process(pid)
    root.suspend()
    children = root.children(recursive=True)
    for child in children:
        try:
            child.suspend()
        except psutil.NoSuchProcess:
            pass
    for p in reversed([root] + children):
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    _, live = psutil.wait_procs([root] + children, timeout=10)
    if live:
        raise RuntimeError('Process tree did not stop')


class Process:
    def __init__(self, command, stdout, stderr, cwd, env):
        self.job = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes as w
            class Basic(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                            ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', w.DWORD),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
            class Extended(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                            ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
            k = ctypes.WinDLL('kernel32', use_last_error=True)
            k.CreateJobObjectW.restype = w.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
            k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
            k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            k.CloseHandle.argtypes = [w.HANDLE]
            k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
            self.k = k
            self.job = k.CreateJobObjectW(None, None)
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not self.job or not k.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.p = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=cwd, env=env,
                                      creationflags=0x4 if os.name == 'nt' else 0, start_new_session=os.name != 'nt')
        except BaseException:
            if self.job:
                self.k.CloseHandle(self.job)
            raise
        self.birth = psutil.Process(self.p.pid).create_time()
        if self.job:
            if not self.k.AssignProcessToJobObject(self.job, int(self.p._handle)):
                self.p.kill()
                self.k.CloseHandle(self.job)
                raise RuntimeError('Cannot contain engine process in Job Object')
            psutil.Process(self.p.pid).resume()

    def close(self):
        # Capture descendants before job termination; verify they have stopped.
        try:
            children = psutil.Process(self.p.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        if self.job:
            import ctypes
            from ctypes import wintypes as w
            class Accounting(ctypes.Structure):
                _fields_ = [('total_user', ctypes.c_int64), ('total_kernel', ctypes.c_int64),
                            ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
                            ('faults', w.DWORD), ('total', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]
            self.k.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
            try:
                if not self.k.TerminateJobObject(self.job, 1):
                    raise RuntimeError('Job termination failed')
                deadline = time.monotonic() + 10
                while True:
                    info = Accounting()
                    if not self.k.QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                        raise RuntimeError('Cannot confirm Job process count')
                    if info.active == 0:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Job still has active descendants')
                    time.sleep(.01)
            finally:
                self.k.CloseHandle(self.job)
                self.job = None
        else:
            if os.name != 'nt':
                import signal
                try:
                    os.killpg(self.p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stop_tree(self.p.pid, self.birth)
        self.p.wait(timeout=10)
        _, live = psutil.wait_procs(children, timeout=10)
        if live:
            raise RuntimeError('Descendants still running after termination')


def execute(command, folder, timeout=300, tick=None):
    from pathlib import Path
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    env.pop('BORDEN3D_SCENE_CONFIG', None)
    with (folder / 'stdout.log').open('w', encoding='utf-8') as out, (folder / 'stderr.log').open('w', encoding='utf-8') as err:
        from .core import ROOT, Problem
        process = Process(command, out, err, ROOT, env)
        start = time.monotonic()
        try:
            while True:
                if tick:
                    tick(process)
                code = process.p.poll()
                if code is not None:
                    return code
                if time.monotonic() - start > timeout:
                    raise Problem('TIMEOUT', 'Stage execution timed out')
                time.sleep(.05)
        finally:
            process.close()
