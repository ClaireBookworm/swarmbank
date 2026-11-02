"""Run as the installed swarm user with its systemd user bus available.

No model calls, login changes, research steering or publications. The serve
process must already be running. Tests use the production sandbox builder.
"""
import concurrent.futures
import json
import subprocess
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from quo_swarm import config, runner, scheduler, store

cfg = config.load()
account = next(a for a in cfg["accounts"] if a["provider"] == "codex")
pool = config.pools(cfg)["science"]


def launch(args, *, memory=None):
    with tempfile.TemporaryDirectory(prefix="probe-", dir=config.RUNS) as folder:
        run = Path(folder)
        for sub in ("home/.sci", "work", "tmp"):
            (run / sub).mkdir(parents=True, exist_ok=True)
        command = runner.sandbox_command(account, cfg, run.name, None, False, pool)
        command = command[:command.index("--chdir") + 3] + args
        if memory:
            command.remove("--collect")  # retain failed scope long enough to inspect Result
            command = ["-pMemoryMax=" + memory if a.startswith("-pMemoryMax=") else
                       "-pMemoryHigh=" + memory if a.startswith("-pMemoryHigh=") else a for a in command]
        result = subprocess.run(command, text=True, capture_output=True, timeout=180)
        result.scope_unit = f"quo-run-{run.name}.scope"
        return result


def checked(args):
    out = launch(args)
    if out.returncode:
        raise RuntimeError(f"sandbox exited {out.returncode}\n{out.stdout}\n{out.stderr}")
    return out.stdout


probe = textwrap.dedent('''
    import hashlib, importlib, importlib.metadata, json, os, pathlib, subprocess
    versions = {}
    for module, package in [('numpy','numpy'),('scipy','scipy'),('jax','jax'),
                            ('kwave','k-wave-python'),('jwave','jwave'),('pmcx','pmcx'),
                            ('mne','mne'),('skfem','scikit-fem'),
                            ('blochsimulator','blochsimulator'),('matplotlib','matplotlib')]:
        importlib.import_module(module)
        versions[package] = importlib.metadata.version(package)
    assert os.environ['OMP_NUM_THREADS'] == '4'
    assert os.environ['JAX_PLATFORMS'] == 'cpu'
    assert 'sci' in subprocess.check_output(['sci','--help'],text=True).lower()
    for path in ['/root/.codex/auth.json', '/home/agent/.sci/../../other-profile',
                 '/opt/quo-data/index.json']:
        assert not pathlib.Path(path).exists(), path
    paths = [subprocess.check_output(['fetch','https://raw.githubusercontent.com/jax-ml/jax/main/README.md'],
                                    text=True).strip() for _ in range(2)]
    assert paths[0] == paths[1]
    downloaded = pathlib.Path(paths[0])
    assert downloaded.is_file()
    for directory in ['/opt/quo-data','/opt/quo-data/objects',str(downloaded.parent)]:
        try:
            os.listdir(directory)
        except PermissionError:
            pass
        else:
            raise AssertionError('listable: ' + directory)
    for action in [lambda: downloaded.write_bytes(b'corrupt'),
                   lambda: downloaded.chmod(0o644),
                   lambda: downloaded.parent.chmod(0o755),
                   lambda: pathlib.Path('/run/quo-gpu.lock').write_text('message')]:
        try:
            action()
        except OSError:
            pass
        else:
            raise AssertionError('shared store or GPU lock writable')
    print(json.dumps({'imports':versions,'fetch_bytes':downloaded.stat().st_size,
                      'fetch_sha256':hashlib.sha256(downloaded.read_bytes()).hexdigest(),
                      'listing':'denied','shared_writes':'denied'}),flush=True)
''')
print(checked(["python", "-c", probe]), flush=True)

gpu = textwrap.dedent('''
    import json, jax, jax.numpy as jnp, numpy as np, pmcx
    devices = jax.devices()
    assert any(d.platform == 'gpu' for d in devices), devices
    assert float(jax.jit(lambda x: x @ x)(jnp.ones((64,64))).block_until_ready()[0,0]) == 64
    info = pmcx.gpuinfo()
    assert info, 'MCX found no GPU'
    result = pmcx.run(nphoton=10000, vol=np.ones((16,16,16),dtype='uint8'),
                      tstart=0, tend=5e-9,tstep=5e-9,srcpos=[8,8,0],srcdir=[0,0,1],
                      prop=np.array([[0,0,1,1],[0.005,1,0.01,1.37]]),gpuid=1)
    assert np.isfinite(result['flux']).all() and result['flux'].sum() > 0
    print(json.dumps({'jax_devices':[str(d) for d in devices],
                      'mcx_devices':len(info),'mcx_flux_sum':float(result['flux'].sum())}),flush=True)
''')
print(checked(["gpu-run", "python", "-c", gpu]), flush=True)

timed = "import json,time; start=time.monotonic(); time.sleep(2); print(json.dumps([start,time.monotonic()]))"
with concurrent.futures.ThreadPoolExecutor(2) as executor:
    intervals = list(executor.map(lambda _: json.loads(checked(["gpu-run", "python", "-c", timed])), range(2)))
intervals.sort()
assert intervals[1][0] >= intervals[0][1], intervals
print(json.dumps({"gpu_serialization": "passed", "intervals": intervals}), flush=True)

# Prove cgroup enforcement with a bounded small test rather than exhausting host RAM.
out = launch(["python", "-c", "x=bytearray(512*1024*1024); print('unexpected allocation')"], memory="128M")
try:
    result = subprocess.check_output(["systemctl", "--user", "show", out.scope_unit,
                                      "--property=Result", "--value"],text=True).strip()
    assert result == "oom-kill" and 'unexpected allocation' not in out.stdout, (result,out.returncode,out.stderr)
    print(json.dumps({"memory_limit_probe": result, "limit": "128M", "exit_code": out.returncode}), flush=True)
finally:
    subprocess.run(["systemctl", "--user", "reset-failed", out.scope_unit], capture_output=True)

# A real orphan scope plus an isolated test DB: never touch research run state.
with tempfile.TemporaryDirectory(prefix="recovery-check-") as folder:
    run_id = "probe-" + uuid.uuid4().hex
    unit = f"quo-run-{run_id}.scope"
    orphan = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "--collect",
                               "--unit=" + unit, "sleep", "60"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            state = subprocess.run(["systemctl", "--user", "is-active", unit], text=True, capture_output=True)
            if state.stdout.strip() == "active":
                break
            time.sleep(0.1)
        else:
            raise AssertionError("recovery fixture scope failed to start")
        with patch.object(config, "DB", Path(folder) / "test.sqlite"):
            with store.connect() as conn:
                conn.execute("insert into runs(id,account,provider,session_id,state,created_at,updated_at) "
                             "values (?,?,?,'fixture-session','running',?,?)",
                             (run_id, "fixture-account", "codex", time.time(), time.time()))
            scheduler.Orchestrator().recover()
            orphan.wait(timeout=10)
            with store.connect() as conn:
                row = conn.execute("select state,session_id from runs where id=?", (run_id,)).fetchone()
            assert row["state"] == "waiting_resume" and row["session_id"] == "fixture-session"
            print(json.dumps({"orphan_scope_recovery": "stopped before same-session requeue"}), flush=True)
    finally:
        if orphan.poll() is None:
            subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", unit], capture_output=True)
            orphan.wait(timeout=10)
