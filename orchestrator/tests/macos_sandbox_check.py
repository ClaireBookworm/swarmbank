"""macOS Seatbelt isolation check against the real install (run: PYTHONPATH=. python tests/macos_sandbox_check.py from /tmp)."""
import subprocess, sys
from pathlib import Path
from quo_swarm import macos, guard, config
run = config.RUNS / "selftest"; acct = config.ACCOUNTS / "selftest"
for d in (run/"work", run/"tmp", run/"home/.sci", acct): d.mkdir(parents=True, exist_ok=True)
key = Path.home()/".sci/key.pem"; H = str(Path.home())
sb = macos.write_profile(run, acct, guard.claude_binary(), key, [Path.home()/"Documents/sci-01/data"])
env = guard.clean_env({"PATH": f"{macos.SCI_VENV}/bin:/usr/bin:/bin", "TMPDIR": str(run/"tmp"), "SCI_HOME": str(run/"home/.sci")}, home=str(run/"home"))
fails = 0
def t(name, cmd, expect_ok):
    global fails
    r = subprocess.run(macos.wrap(["/bin/sh","-c",cmd], sb), env=env, cwd=run/"work", capture_output=True, text=True)
    ok = r.returncode == 0; fails += ok != expect_ok
    print(("PASS" if ok == expect_ok else "FAIL"), f"{name:<28}", "allowed" if ok else "blocked", "" if ok == expect_ok else r.stderr.strip()[-120:])
t("write work", "echo x > out.txt && cat out.txt >/dev/null", True)
t("write own tmp", 'echo x > "$TMPDIR/a"', True)
t("read signing key", f"head -c 10 {key} >/dev/null", True)
t("read shared data", f"ls {H}/Documents/sci-01/data/flywire >/dev/null", True)
t("write shared data", f"touch {H}/Documents/sci-01/data/x", False)
t("read public key (home)", f"cat {H}/.sci/key.pub.pem", False)
t("read main claude creds", f"cat {H}/.claude/.credentials.json", False)
t("list home", f"ls {H}", False)
t("read other account", f"ls {config.ACCOUNTS}", False)
t("read orchestrator db", f"ls {config.STATE}", False)
t("read /private/tmp", "ls /private/tmp/claude-502", False)
t("write /usr/local", "touch /usr/local/x_quo", False)
t("python+sci", "python -c 'import sci,numpy,pandas' && sci --help >/dev/null", True)
t("uv venv in work", f"{macos.TOOLS}/uv venv -q --python {macos.SCI_VENV}/bin/python v && v/bin/python -c 1", True)
t("network", "curl -s -o /dev/null -w '%{http_code}' https://sci.azaho.org/ | grep -q 200", True)
t("claude binary runs", f"{guard.claude_binary()} --version", True)
import shutil; shutil.rmtree(run); shutil.rmtree(acct)
print("FAILURES:", fails); sys.exit(fails)
